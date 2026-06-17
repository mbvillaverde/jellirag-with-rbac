"""Own-history access (capability: rag-chat, auth owner-scoping).

History reads are owner-scoped: owner_email comes from the caller's JWT. The
broker JOINs sessions.owner_email, so a session_id owned by another user yields
no rows (cross-owner access behaves as "new session").
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..security.deps import Principal, get_broker, require_role
from ..services.broker_client import BrokerClient, BrokerError

router = APIRouter(tags=["history"])

DEFAULT_HISTORY_TOKENS = 2000


@router.get("/history/{session_id}")
async def read_history(
    session_id: str,
    max_tokens: int = DEFAULT_HISTORY_TOKENS,
    principal: Principal = Depends(require_role("member")),
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, Any]:
    try:
        history = await broker.history_read(session_id, principal.email, max_tokens)
    except BrokerError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "history read failed")
    return {"session_id": session_id, "history": history}
