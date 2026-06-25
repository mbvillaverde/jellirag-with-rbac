"""Scheduled jobs (capability: library-sync + rag-chat, task 5.7 + 8.2).

APScheduler runs library sync and session prune on the configured cron inside
the FastAPI process. Both jobs use the shared clients stored on app.state.
Manual triggers remain available (admin-only) for ad-hoc runs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config.settings import Settings
from ..services.ai_provider import EmbeddingsClient
from ..services.db import Database
from ..services.jellyfin_client import JellyfinClient
from ..services.sync_service import run_library_sync

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger("jellirag.scheduler")


def _make_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone="UTC")


async def _job_sync(embed: EmbeddingsClient, db: Database, jellyfin: JellyfinClient) -> None:
    log.info("scheduled sync starting")
    try:
        summary = await run_library_sync(embed, db, jellyfin)
        log.info("scheduled sync done: %s", summary.as_dict())
    except Exception as exc:  # never crash the scheduler
        log.warning("scheduled sync failed: %s", exc)


async def _job_prune(db: Database, settings: Settings) -> None:
    if settings.session_ttl_days <= 0:
        return
    older_than = (datetime.now(timezone.utc) - timedelta(days=settings.session_ttl_days)).isoformat()
    try:
        counts = await db.sessions_prune(older_than)
        log.info("scheduled prune done: %s", counts)
    except Exception as exc:
        log.warning("scheduled prune failed: %s", exc)


def start_scheduler(app: "FastAPI", settings: Settings) -> AsyncIOScheduler:
    scheduler = _make_scheduler()
    embed: EmbeddingsClient = app.state.embed
    db: Database = app.state.db
    jellyfin: JellyfinClient = app.state.jellyfin

    try:
        scheduler.add_job(
            _job_sync,
            CronTrigger.from_crontab(settings.sync_cron),
            id="sync",
            args=[embed, db, jellyfin],
            replace_existing=True,
        )
    except Exception as exc:
        log.warning("invalid SYNC_CRON %r: %s", settings.sync_cron, exc)

    # Prune runs daily shortly after midnight UTC (independent of SYNC_CRON).
    scheduler.add_job(
        _job_prune,
        CronTrigger(hour=4, minute=15),
        id="prune",
        args=[db, settings],
        replace_existing=True,
    )

    scheduler.start()
    return scheduler
