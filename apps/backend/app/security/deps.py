"""FastAPI dependencies: shared clients, authenticated principal, RBAC, and
per-email login rate limiting.

The principal (`email`/`role`) is derived solely from the caller's JWT — never
from the request body — so cross-owner history access behaves as "new session".
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config.settings import Settings, get_settings
from ..security.jwt import decode_token
from ..services.broker_client import BrokerClient
from ..services.jellyfin_client import JellyfinClient

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    email: str
    role: str


def get_broker(request: Request) -> BrokerClient:
    return request.app.state.broker


def get_jellyfin(request: Request) -> JellyfinClient:
    return request.app.state.jellyfin


async def _extract_token(request: Request) -> str | None:
    # Authorization: Bearer <jwt> preferred; fall back to ?token= for SSE (EventSource
    # cannot set headers). HttpOnly cookie is also accepted for browser clients.
    creds: HTTPAuthorizationCredentials | None = await _bearer(request)
    if creds and creds.credentials:
        return creds.credentials
    cookie = request.cookies.get("jr_token")
    if cookie:
        return cookie
    return request.query_params.get("token")


async def get_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Principal:
    token = await _extract_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        payload = decode_token(token, settings.jwt_secret)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    email = payload.get("sub")
    role = payload.get("role")
    if not isinstance(email, str) or not isinstance(role, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token claims")
    return Principal(email=email, role=role)


def require_role(min_role: str):
    """Dependency factory: 'admin' gates operational routes; 'member' (any
    authenticated user) gates chat/own-history. 403 on mismatch."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        order = {"member": 1, "admin": 2}
        if order.get(principal.role, 0) < order.get(min_role, 99):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    return _dep


# ---- per-email login rate limiting (compensates for no CF Access shielding) ----
@dataclass
class _Bucket:
    failures: list[float] = field(default_factory=list)


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def _prune(self, key: str, now: float) -> None:
        b = self._buckets[key]
        b.failures = [t for t in b.failures if now - t < self.window]

    def is_limited(self, key: str) -> bool:
        now = time.time()
        self._prune(key, now)
        return len(self._buckets[key].failures) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.time()
        self._prune(key, now)
        self._buckets[key].failures.append(now)


_rate_limiter: LoginRateLimiter | None = None


def get_rate_limiter(settings: Settings = Depends(get_settings)) -> LoginRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)
    return _rate_limiter
