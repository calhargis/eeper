"""JWTs (short-lived access + TOTP challenge) and opaque refresh/api tokens."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

import jwt

_ALGORITHM = "HS256"


def _encode(secret: str, payload: dict[str, Any]) -> str:
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def _decode(secret: str, token: str) -> dict[str, Any] | None:
    try:
        # jwt.decode validates the signature and `exp`, so expired tokens fail here.
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None


def _claims(user_id: int, token_type: str, now: datetime, ttl_seconds: int) -> dict[str, Any]:
    return {
        "sub": str(user_id),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }


def create_access_token(
    secret: str, *, user_id: int, role: str, now: datetime, ttl_seconds: int
) -> str:
    payload = _claims(user_id, "access", now, ttl_seconds)
    payload["role"] = role
    return _encode(secret, payload)


def decode_access_token(secret: str, token: str) -> dict[str, Any] | None:
    payload = _decode(secret, token)
    if payload is None or payload.get("type") != "access":
        return None
    return payload


def create_totp_challenge(secret: str, *, user_id: int, now: datetime, ttl_seconds: int) -> str:
    return _encode(secret, _claims(user_id, "totp_challenge", now, ttl_seconds))


def decode_totp_challenge(secret: str, token: str) -> int | None:
    payload = _decode(secret, token)
    if payload is None or payload.get("type") != "totp_challenge":
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


def generate_opaque_token() -> str:
    """A URL-safe, high-entropy secret for refresh tokens and API tokens."""
    return secrets.token_urlsafe(32)
