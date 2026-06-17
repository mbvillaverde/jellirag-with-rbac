"""JWT issuance + verification (capability: auth).

`JWT_SECRET` signs tokens carrying `{sub: email, role, exp}`. TTL is governed by
`JWT_TTL_DAYS` (default 7). Rotating `JWT_SECRET` (Dokploy env + restart)
invalidates all outstanding tokens immediately without touching any other
credential.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
from jwt import InvalidTokenError

ALGORITHM = "HS256"


def issue_token(email: str, role: str, secret: str, ttl_days: int) -> str:
    now = int(time.time())
    payload = {"sub": email, "role": role, "iat": now, "exp": now + ttl_days * 86400}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """Decode + verify signature + expiration. Raises InvalidTokenError on any failure."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
