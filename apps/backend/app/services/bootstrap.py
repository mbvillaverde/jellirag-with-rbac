"""Idempotent bootstrap-admin provisioning (capability: auth).

Run at FastAPI startup. If the `users` table is empty AND both
`BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD` are set, insert an admin
row with an argon2id hash of the bootstrap password. If the table is non-empty,
do nothing regardless of env values. The bootstrap credentials are used once to
seed the row; normal login applies thereafter.
"""
from __future__ import annotations

import logging

from ..config.settings import Settings
from ..security.passwords import hash_password
from ..services.db import Database

log = logging.getLogger("jellirag.bootstrap")


async def ensure_bootstrap_admin(db: Database, settings: Settings) -> None:
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if not email or not password:
        return

    try:
        existing = await db.users_list()
    except Exception as exc:
        log.warning("bootstrap: cannot reach db to check users: %s", exc)
        return

    if existing:
        # Non-empty table => never seed, even if env values are present.
        log.info("bootstrap: users table non-empty (%d rows); skipping", len(existing))
        return

    try:
        await db.users_create(email, "admin", hash_password(password))
        log.info("bootstrap: provisioned admin %s", email)
    except Exception as exc:
        log.warning("bootstrap: failed to create admin: %s", exc)
