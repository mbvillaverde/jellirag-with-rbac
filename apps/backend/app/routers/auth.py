"""Login endpoint (capability: auth).

Issues a role-bearing JWT after email/password verification against the SQLite
users table. Password verification happens locally in FastAPI (argon2id);
the database stores only the opaque hash.

The 401 response shape and timing are identical for unknown-user vs
wrong-password: when the user is absent we still perform a dummy argon2 verify
so the response time matches a real verification.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from ..config.settings import Settings, get_settings
from ..security.deps import LoginRateLimiter, get_rate_limiter
from ..security.jwt import issue_token
from ..security.passwords import _hasher, verify_password
from ..services.db import Database


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


router = APIRouter(tags=["auth"])

# A real argon2id hash of a throwaway value, used only to equalize timing when
# the requested user does not exist. Generated lazily on first use.
_DUMMY_HASH: str | None = None


def _dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hasher.hash("jellirag-timing-equalization-placeholder")
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except Exception:
        pass


def _reject() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")


def get_db(request: Request) -> Database:
    return request.app.state.db


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    limiter: LoginRateLimiter = Depends(get_rate_limiter),
) -> dict:
    email = body.email.strip().lower()
    password = body.password
    if not email or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email and password required")

    if limiter.is_limited(email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts")

    try:
        user = await db.users_lookup(email)
    except Exception:
        limiter.record_failure(email)
        raise _reject()

    if user is None:
        _dummy_verify(password)  # equalize timing with the real-verify path
        limiter.record_failure(email)
        raise _reject()

    if not verify_password(password, user["pw_hash"]):
        limiter.record_failure(email)
        raise _reject()

    token = issue_token(email, user["role"], settings.jwt_secret, settings.jwt_ttl_days)
    response.set_cookie(
        key="jr_token", value=token, httponly=True, samesite="lax", secure=False, max_age=settings.jwt_ttl_days * 86400
    )
    return {"token": token, "role": user["role"], "email": email}
