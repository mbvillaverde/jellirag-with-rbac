"""Sync trigger + session prune + scheduled jobs (capabilities: library-sync,
rag-chat, tasks 5.7 + 8.2).

Manual sync is admin-only and returns a status summary. Scheduled sync + prune
run on the configured cron (APScheduler) inside the FastAPI process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..config.settings import Settings, get_settings
from ..security.deps import Principal, get_broker, get_jellyfin, require_role
from ..services.broker_client import BrokerClient, BrokerError
from ..services.jellyfin_client import JellyfinClient
from ..services.sync_service import SyncFailed, run_library_sync

router = APIRouter(tags=["ops"])


@router.post("/sync")
async def trigger_sync(
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
    jellyfin: JellyfinClient = Depends(get_jellyfin),
) -> dict[str, Any]:
    try:
        summary = await run_library_sync(broker, jellyfin)
    except SyncFailed as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except BrokerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"broker error: {exc.message}")
    return summary.as_dict()


@router.post("/sessions/prune")
async def prune_sessions(
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    # 0 disables pruning (retain forever).
    if settings.session_ttl_days <= 0:
        return {"deleted_sessions": 0, "deleted_messages": 0, "note": "prune disabled (TTL=0)"}
    older_than = (datetime.now(timezone.utc) - timedelta(days=settings.session_ttl_days)).isoformat()
    try:
        return await broker.sessions_prune(older_than)
    except BrokerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"broker error: {exc.message}")
