"""Streaming chat endpoint (capability: rag-chat).

The retrieval-and-generation path is exactly two broker calls: /prepare-rag
(fused reads) then /llm-stream. A post-stream /history/append persists the
completed turns and is explicitly NOT on the latency-critical path.

History is owner-scoped: owner_email comes from the caller's JWT, not the
request body. A session_id owned by another user behaves as "new session" — the
broker returns no history for it.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..budget.manager import (
    CONTEXT_CAP,
    HISTORY_REQUEST_BUDGET,
    RESPONSE_HEADROOM,
    reconcile_and_assemble,
)
from ..config.settings import Settings, get_settings
from ..security.deps import Principal, get_broker, require_role
from ..services.broker_client import BrokerClient, BrokerError
from ..services.prompts import build_system_prompt
from ..services.sse import reframe_sse

router = APIRouter(tags=["chat"])

TOP_K = 8


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: dict,
    principal: Principal = Depends(require_role("member")),
    broker: BrokerClient = Depends(get_broker),
    settings: Settings = Depends(get_settings),
):
    session_id = str(body.get("session_id", "")).strip()
    message = str(body.get("message", "")).strip()
    if not session_id or not message:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "session_id and message required")

    owner_email = principal.email  # JWT-derived, never from body

    system_prompt = build_system_prompt(settings.jellyfin_deeplink_base)

    # ---- HOT PATH call 1: fused retrieval (embed + Vectorize + chunks + history) ----
    try:
        prepared = await broker.prepare_rag(
            session_id=session_id,
            owner_email=owner_email,
            message=message,
            top_k=TOP_K,
            history_max_tokens=HISTORY_REQUEST_BUDGET,
        )
    except BrokerError as exc:
        raise HTTPException(_map_status(exc.status), "retrieval failed")

    assembled = reconcile_and_assemble(
        system_prompt=system_prompt,
        history=prepared.history,
        chunks=prepared.chunks,
        user_message=message,
    )

    contributing_jf_ids = assembled.contributing_jf_ids

    async def generate():
        assistant_parts: list[str] = []

        # Emit the contributing sources first so the client can render chips.
        sources_event = json.dumps({"sources": contributing_jf_ids})
        yield f"data: {sources_event}\n\n"

        # ---- HOT PATH call 2: stream the model output ----
        try:
            upstream = await broker.llm_stream(assembled.messages, RESPONSE_HEADROOM)
        except BrokerError:
            yield f"data: {json.dumps({'error': 'generation failed'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        async for sse_event in reframe_sse(upstream.aiter_bytes()):
            # Capture assistant tokens for persistence (skip our terminal/DONE).
            try:
                payload = json.loads(sse_event[len("data: "):].strip())
                if "response" in payload:
                    assistant_parts.append(payload["response"])
            except Exception:
                pass
            yield sse_event

        # ---- Post-stream persistence (NOT on the latency-critical path) ----
        assistant_text = "".join(assistant_parts).strip()
        try:
            await broker.history_append(
                session_id=session_id,
                owner_email=owner_email,
                role="user",
                content=message,
                token_count=(len(message) + 3) // 4,
            )
            if assistant_text:
                await broker.history_append(
                    session_id=session_id,
                    owner_email=owner_email,
                    role="assistant",
                    content=assistant_text,
                    token_count=(len(assistant_text) + 3) // 4,
                )
        except BrokerError:
            pass  # persistence best-effort; do not surface to the client

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _map_status(status_code: int) -> int:
    return status.HTTP_502_BAD_GATEWAY if status_code >= 500 else status.HTTP_400_BAD_REQUEST


# CONTEXT_CAP is referenced here to keep the budget visible at the router level;
# the actual reconciliation lives in budget.manager.
_ = CONTEXT_CAP
