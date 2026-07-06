"""Password hashing (Argon2) and opaque-token hashing.

Refresh tokens and API tokens are high-entropy random strings; we store only
their SHA-256 (no salt needed for uniformly-random secrets) and look them up by
that hash. JWTs are handled in ``tokens.py``.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        # Wrong password, or a stored hash we can't parse — both are auth failures.
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("eeper-dummy-password")


def verify_dummy(password: str) -> None:
    """Verify against a fixed hash to equalize login timing for unknown users
    (mitigates username enumeration via response timing)."""
    verify_password(_dummy_hash(), password)


def hash_token(token: str) -> str:
    """SHA-256 of a high-entropy opaque token, for at-rest storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
