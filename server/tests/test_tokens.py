"""Unit tests for JWT + opaque token helpers (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eeper.api.tokens import (
    create_access_token,
    create_totp_challenge,
    decode_access_token,
    decode_totp_challenge,
    generate_opaque_token,
)

_KEY = "k" * 32


def test_access_token_roundtrip() -> None:
    now = datetime.now(UTC)
    token = create_access_token(_KEY, user_id=7, role="admin", now=now, ttl_seconds=900)
    payload = decode_access_token(_KEY, token)
    assert payload is not None
    assert payload["sub"] == "7"
    assert payload["role"] == "admin"


def test_access_token_expired_is_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = create_access_token(_KEY, user_id=7, role="admin", now=past, ttl_seconds=60)
    assert decode_access_token(_KEY, token) is None


def test_access_token_wrong_key_is_rejected() -> None:
    token = create_access_token(
        _KEY, user_id=7, role="admin", now=datetime.now(UTC), ttl_seconds=900
    )
    assert decode_access_token("a-different-secret-key-0123456789ab", token) is None


def test_access_decode_rejects_wrong_token_type() -> None:
    challenge = create_totp_challenge(_KEY, user_id=7, now=datetime.now(UTC), ttl_seconds=300)
    assert decode_access_token(_KEY, challenge) is None


def test_totp_challenge_roundtrip() -> None:
    challenge = create_totp_challenge(_KEY, user_id=9, now=datetime.now(UTC), ttl_seconds=300)
    assert decode_totp_challenge(_KEY, challenge) == 9


def test_totp_decode_rejects_access_token() -> None:
    token = create_access_token(
        _KEY, user_id=9, role="viewer", now=datetime.now(UTC), ttl_seconds=300
    )
    assert decode_totp_challenge(_KEY, token) is None


def test_opaque_token_is_unique_and_long() -> None:
    a, b = generate_opaque_token(), generate_opaque_token()
    assert a != b
    assert len(a) >= 32
