"""Unit tests for the pure password/token-hashing helpers (no DB, no network)."""

from __future__ import annotations

from eeper.api.security import hash_password, hash_token, verify_password


def test_hash_and_verify_roundtrip() -> None:
    digest = hash_password("s3cret-password-1234")
    assert digest != "s3cret-password-1234"  # not stored in plaintext
    assert verify_password(digest, "s3cret-password-1234") is True
    assert verify_password(digest, "wrong-password") is False


def test_verify_rejects_malformed_hash() -> None:
    assert verify_password("not-a-real-argon2-hash", "whatever") is False


def test_hash_token_is_stable_and_distinct() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64  # sha256 hex digest
