"""Password hashing (Argon2) and signed session tokens.

Deliberately small for M0.2. The session is an HMAC-signed, time-limited token
(via itsdangerous) carried in an httpOnly, Secure cookie — enough to gate
endpoints with 401. M0.3 replaces this with JWT access/refresh + TOTP.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_hasher = PasswordHasher()

_SESSION_SALT = "eeper.session.v1"


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


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_SESSION_SALT)


def issue_session(secret_key: str, user_id: int) -> str:
    """Return a signed session token for ``user_id``."""
    return _serializer(secret_key).dumps({"uid": user_id})


def read_session(secret_key: str, token: str, max_age_seconds: int) -> int | None:
    """Return the user id from a valid, unexpired token, else ``None``."""
    try:
        data: Any = _serializer(secret_key).loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None
