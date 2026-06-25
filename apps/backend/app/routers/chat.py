"""Streaming chat endpoint (capability: rag-chat).

The retrieval-and-generation path is: embed → vector_search → history_read → llm_stream.
Post-stream /history/append persists the completed turns and is explicitly NOT on the latency-critical path.

History is owner-scoped: owner_email comes from the caller's JWT, not the request body.
A session_id owned by another user behaves as "new session" — no history returned.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..budget.manager import (
    CONTEXT_CAP,
    HISTORY_REQUEST_BUDGET,
    RESPONSE_HEADROOM,
    reconcile_and_assemble,
)
from ..config.settings import Settings, get_settings
from ..security.deps import Principal, require_role
from ..services.ai_provider import AIProviderError, LLMClient, EmbeddingsClient
from ..services.db import Database
from ..services.prompts import build_system_prompt
from ..services.sse import reframe_sse


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


router = APIRouter(tags=["chat"])

TOP_K = 8


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_embed(request: Request) -> EmbeddingsClient:
    return request.app.state.embed


def get_db(request: Request) -> Database:
    return request.app.state.db


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    principal: Principal = Depends(require_role("member")),
    llm: LLMClient = Depends(get_llm),
    embed: EmbeddingsClient = Depends(get_embed),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    session_id = body.session_id.strip()
    message = body.message.strip()
    if not session_id or not message:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id and message required")

    owner_email = principal.email  # JWT-derived, never from body

    system_prompt = build_system_prompt(settings.jellyfin_deeplink_base)

    # ---- HOT PATH call 1: embed the query ----
    try:
        query_vecs = await embed.embed([message])
        if not query_vecs:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "embedding failed")
        query_vec = query_vecs[0]
    except AIProviderError as exc:
        raise HTTPException(_map_status(exc.status), "embedding failed")

    # ---- HOT PATH call 2: vector search ----
    try:
        chunks = await db.vector_search(query_vec, TOP_K)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"vector search failed: {exc}")

    # ---- HOT PATH call 3: read history ----
    try:
        history = await db.history_read(session_id, owner_email, HISTORY_REQUEST_BUDGET)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"history read failed: {exc}")

    assembled = reconcile_and_assemble(
        system_prompt=system_prompt,
        history=history,
        chunks=[{
            "chunk_text": c["chunk_text"],
            "jf_id": c["jf_id"],
            "title": c["title"],
            "year": c["year"],
            "genres": c["genres"],
        } for c in chunks],
        user_message=message,
    )

    contributing_jf_ids = assembled.contributing_jf_ids

    # Build per-source metadata for rich chip rendering from the already-fetched
    # chunks (zero extra queries). Only contributing ids are emitted.
    contributing_set = set(contributing_jf_ids)
    source_metadata: dict[str, dict[str, Any]] = {}
    for c in chunks:
        jf_id = c["jf_id"]
        if jf_id in contributing_set and jf_id not in source_metadata:
            source_metadata[jf_id] = {
                "title": c.get("title"),
                "year": c.get("year"),
                "genres": c.get("genres"),
                "image_url": f"/api/jellyfin/image?id={jf_id}",
            }

    async def generate():
        assistant_parts: list[str] = []

        # Emit the contributing sources first so the client can render chips.
        # `source_metadata` is sent alongside `sources` so chips can render
        # title/year/genres and lazy-load thumbnails without a second round-trip.
        sources_event = json.dumps(
            {"sources": contributing_jf_ids, "source_metadata": source_metadata}
        )
        yield f"data: {sources_event}\n\n"

        # ---- HOT PATH call 4: stream the model output ----
        try:
            async for token in llm.stream_chat(assembled.messages, RESPONSE_HEADROOM):
                payload = {"response": token}
                sse_event = f"data: {json.dumps(payload)}\n\n"
                assistant_parts.append(token)
                yield sse_event
        except AIProviderError:
            yield f"data: {json.dumps({'error': 'generation failed'})}\n\n"
            return

        yield "data: [DONE]\n\n"

        # ---- Post-stream persistence (NOT on the latency-critical path) ----
        assistant_text = "".join(assistant_parts).strip()
        try:
            await db.history_append(
                session_id=session_id,
                owner_email=owner_email,
                role="user",
                content=message,
                token_count=(len(message) + 3) // 4,
            )
            if assistant_text:
                await db.history_append(
                    session_id=session_id,
                    owner_email=owner_email,
                    role="assistant",
                    content=assistant_text,
                    token_count=(len(assistant_text) + 3) // 4,
                )
        except Exception:
            pass  # persistence best-effort; do not surface to the client

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _map_status(status_code: int) -> int:
    return status.HTTP_503_SERVICE_UNAVAILABLE if status_code >= 500 else status.HTTP_400_BAD_REQUEST


# CONTEXT_CAP is referenced here to keep the budget visible at the router level;
# the actual reconciliation lives in budget.manager.
_ = CONTEXT_CAP
