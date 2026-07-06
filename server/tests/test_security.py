"""Unit tests for the pure auth helpers (no DB, no network)."""

from __future__ import annotations

from eeper.api.security import (
    hash_password,
    issue_session,
    read_session,
    verify_password,
)

_KEY = "k" * 32


def test_hash_and_verify_roundtrip() -> None:
    digest = hash_password("s3cret-password")
    assert digest != "s3cret-password"  # not stored in plaintext
    assert verify_password(digest, "s3cret-password") is True
    assert verify_password(digest, "wrong-password") is False


def test_verify_rejects_malformed_hash() -> None:
    assert verify_password("not-a-real-argon2-hash", "whatever") is False


def test_session_token_roundtrip() -> None:
    token = issue_session(_KEY, 42)
    assert read_session(_KEY, token, max_age_seconds=3600) == 42


def test_session_token_rejects_wrong_key() -> None:
    token = issue_session(_KEY, 42)
    assert read_session("a-different-secret-key", token, max_age_seconds=3600) is None


def test_session_token_rejects_tampering() -> None:
    token = issue_session(_KEY, 42)
    assert read_session(_KEY, token + "tampered", max_age_seconds=3600) is None
