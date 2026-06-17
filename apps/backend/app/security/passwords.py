"""Password hashing (argon2id) performed by FastAPI only — never by the broker.

Argon2id is the memory-hard PHC winner; argon2-cffi exposes a constant-time
verify. Used by login verification, account create, and password reset.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()  # argon2-cffi defaults to argon2id


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return _hasher.verify(pw_hash, password)
    except VerifyMismatchError:
        return False
