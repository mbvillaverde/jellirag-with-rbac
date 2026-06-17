"""Admin account provisioning (capability: auth).

All routes require the `admin` role. Creating a user hashes the password in
FastAPI (argon2id) and sends only the opaque `pw_hash` to the broker. Listing
returns no hashes (broker never returns them on list). Deleting cascades to the
user's sessions + messages (broker enforces the FK cascade).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..security.deps import Principal, get_broker, require_role
from ..security.passwords import hash_password
from ..services.broker_client import BrokerError, BrokerClient

router = APIRouter(tags=["admin"])


@router.get("/users")
async def list_users(
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, Any]:
    return {"users": await broker.users_list()}


@router.post("/users")
async def create_user(
    body: dict,
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, str]:
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    role = str(body.get("role", "member"))
    if not email or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email and password required")
    if role not in ("admin", "member"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "role must be admin|member")
    try:
        await broker.users_create(email, role, hash_password(password))
    except BrokerError as exc:
        if exc.status == 409:
            raise HTTPException(status.HTTP_409_CONFLICT, "user exists")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "broker error")
    return {"ok": "created"}


@router.put("/users/{email}")
async def update_user(
    email: str,
    body: dict,
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, str]:
    role = body.get("role")
    password = body.get("password")
    if role is None and password is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to update")
    if role is not None and role not in ("admin", "member"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "role must be admin|member")
    pw_hash = hash_password(str(password)) if password is not None else None
    try:
        await broker.users_update(email.lower(), role=role, pw_hash=pw_hash)
    except BrokerError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "broker error")
    return {"ok": "updated"}


@router.delete("/users/{email}")
async def delete_user(
    email: str,
    principal: Principal = Depends(require_role("admin")),
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, Any]:
    try:
        counts = await broker.users_delete(email.lower())
    except BrokerError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "broker error")
    return counts
