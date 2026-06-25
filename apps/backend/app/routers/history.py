"""Own-history access (capability: rag-chat, auth owner-scoping).

History reads are owner-scoped: owner_email comes from the caller's JWT. The
SQL query joins sessions.owner_email, so a session_id owned by another user yields
no rows (cross-owner access behaves as "new session").
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..security.deps import Principal, require_role
from ..services.db import Database

router = APIRouter(tags=["history"])

DEFAULT_HISTORY_TOKENS = 2000


def get_db(request: Request) -> Database:
    return request.app.state.db


@router.get("/history/{session_id}")
async def read_history(
    session_id: str,
    max_tokens: int = DEFAULT_HISTORY_TOKENS,
    principal: Principal = Depends(require_role("member")),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        history = await db.history_read(session_id, principal.email, max_tokens)
    except Exception:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "history read failed")
    return {"session_id": session_id, "history": history}
