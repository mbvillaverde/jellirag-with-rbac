"""Admin account provisioning (capability: auth).

All routes require the `admin` role. Creating a user hashes the password in
FastAPI (argon2id) and stores only the opaque `pw_hash` in SQLite. Listing
returns no hashes. Deleting cascades to the user's sessions + messages
(enforced by SQLite FK cascade).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from ..security.deps import Principal, require_role
from ..security.passwords import hash_password
from ..services.db import Database


class CreateUserRequest(BaseModel):
    email: EmailStr = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role: str = Field(default="member", pattern="^(admin|member)$")


class UpdateUserRequest(BaseModel):
    role: str | None = Field(None, pattern="^(admin|member)$")
    password: str | None = None


router = APIRouter(tags=["admin"])


def get_db(request: Request) -> Database:
    return request.app.state.db


@router.get("/users")
async def list_users(
    principal: Principal = Depends(require_role("admin")),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    return {"users": await db.users_list()}


@router.post("/users")
async def create_user(
    body: CreateUserRequest,
    principal: Principal = Depends(require_role("admin")),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    try:
        await db.users_create(body.email.lower(), body.role, hash_password(body.password))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, "user exists")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "creation failed")
    return {"ok": "created"}


@router.put("/users/{email}")
async def update_user(
    email: str,
    body: UpdateUserRequest,
    principal: Principal = Depends(require_role("admin")),
    db: Database = Depends(get_db),
) -> dict[str, str]:
    if body.role is None and body.password is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to update")
    pw_hash = hash_password(str(body.password)) if body.password is not None else None
    try:
        await db.users_update(email.lower(), role=body.role, pw_hash=pw_hash)
    except Exception:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "update failed")
    return {"ok": "updated"}


@router.delete("/users/{email}")
async def delete_user(
    email: str,
    principal: Principal = Depends(require_role("admin")),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    try:
        counts = await db.users_delete(email.lower())
    except Exception:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed")
    return counts
