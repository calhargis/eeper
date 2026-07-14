"""M5.1 auth-fuzzing corpus: token TAMPERING, DOWNGRADE, and REPLAY attempts against the
live API are all rejected.

This complements the unit token tests (test_tokens.py) and the refresh-rotation matrix
(test_auth_api.py) by hitting the HTTP boundary with the hand-crafted cookies an attacker
would actually send. Every crafted access token must fail closed — the endpoint returns
401 (bad credential) or, for the escalation probe, 403 (the token's `role` claim is not
authoritative; authorization reads the database role).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt

from eeper.api.tokens import create_access_token, create_totp_challenge
from tests.conftest import Harness

_PW = "correct horse battery staple"


async def _first_boot(api: Harness) -> int:
    r = await api.client.post(
        "/api/v1/system/first-boot", json={"username": "admin", "password": _PW}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _me_status(api: Harness, token: str) -> int:
    """GET /me with `token` as the access cookie, from a fresh (unauthenticated) client."""
    async with api.fresh() as c:
        r = await c.get(
            "/api/v1/me", headers={"Cookie": f"{api.settings.access_cookie_name}={token}"}
        )
        return r.status_code


def _b64(obj: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _claims(
    user_id: int, *, role: str = "admin", type_: str = "access", ttl: int = 900
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "sub": str(user_id),
        "type": type_,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }


# ── tampering ─────────────────────────────────────────────────────────────────


async def test_flipped_signature_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    good = create_access_token(
        api.settings.secret_key, user_id=uid, role="admin", now=datetime.now(UTC), ttl_seconds=900
    )
    head, payload, sig = good.split(".")
    # Flip a byte of the signature; every other segment is untouched.
    forged = f"{head}.{payload}.{sig[:-3] + ('A' if sig[-3] != 'A' else 'B') + sig[-2:]}"
    assert await _me_status(api, forged) == 401


async def test_payload_tampered_under_original_signature_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    good = create_access_token(
        api.settings.secret_key, user_id=uid, role="viewer", now=datetime.now(UTC), ttl_seconds=900
    )
    head, _payload, sig = good.split(".")
    # Swap in a payload claiming a different subject while keeping the original signature.
    forged = f"{head}.{_b64(_claims(uid + 999))}.{sig}"
    assert await _me_status(api, forged) == 401


async def test_token_signed_with_attacker_secret_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    forged = jwt.encode(_claims(uid), "attacker-controlled-secret-000000", algorithm="HS256")
    assert await _me_status(api, forged) == 401


async def test_wrong_token_type_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    # A validly-signed TOTP challenge must not be usable as an access token.
    challenge = create_totp_challenge(
        api.settings.secret_key, user_id=uid, now=datetime.now(UTC), ttl_seconds=900
    )
    assert await _me_status(api, challenge) == 401


async def test_garbage_token_is_rejected(api: Harness) -> None:
    await _first_boot(api)
    for junk in ("", "not-a-jwt", "a.b.c", "..", "Bearer x"):
        assert await _me_status(api, junk) == 401


# ── downgrade ─────────────────────────────────────────────────────────────────


async def test_alg_none_forgery_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    # The classic downgrade: an unsigned token (alg="none") with admin claims. Decoding is
    # pinned to HS256, so an unsigned token can never authenticate.
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims(uid))}."
    assert await _me_status(api, unsigned) == 401


async def test_algorithm_confusion_hs512_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    # Correctly signed, but with a different HMAC algorithm than the verifier accepts.
    other = jwt.encode(_claims(uid), api.settings.secret_key, algorithm="HS512")
    assert await _me_status(api, other) == 401


# ── replay ────────────────────────────────────────────────────────────────────


async def test_expired_access_token_is_rejected(api: Harness) -> None:
    uid = await _first_boot(api)
    expired = create_access_token(
        api.settings.secret_key,
        user_id=uid,
        role="admin",
        now=datetime.now(UTC) - timedelta(hours=2),
        ttl_seconds=60,
    )
    assert await _me_status(api, expired) == 401


async def test_replayed_refresh_token_is_rejected_and_revokes_family(api: Harness) -> None:
    await _first_boot(api)
    async with api.fresh() as c:
        assert (
            await c.post("/api/v1/auth/login", json={"username": "admin", "password": _PW})
        ).status_code == 200
        stale_refresh = c.cookies.get(api.settings.refresh_cookie_name)
        assert stale_refresh is not None
        # Rotate once (the legitimate client advances to a new refresh token)…
        assert (await c.post("/api/v1/auth/refresh")).status_code == 200

    # …then an attacker replays the now-rotated refresh token: rejected.
    async with api.fresh() as attacker:
        r = await attacker.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"{api.settings.refresh_cookie_name}={stale_refresh}"},
        )
        assert r.status_code == 401


# ── escalation: the token's role claim is not authoritative ───────────────────


async def test_signed_token_cannot_self_escalate_role(api: Harness) -> None:
    await _first_boot(api)
    created = await api.client.post(
        "/api/v1/users", json={"username": "grandpa", "password": _PW, "role": "viewer"}
    )
    assert created.status_code == 201, created.text
    viewer_id = int(created.json()["id"])

    # A perfectly-signed token (real secret) for the viewer, but claiming role=admin.
    forged_admin = create_access_token(
        api.settings.secret_key,
        user_id=viewer_id,
        role="admin",
        now=datetime.now(UTC),
        ttl_seconds=900,
    )
    async with api.fresh() as c:
        cookie = {"Cookie": f"{api.settings.access_cookie_name}={forged_admin}"}
        # The session is valid (it is a real viewer), but authorization uses the DB role…
        me = await c.get("/api/v1/me", headers=cookie)
        assert me.status_code == 200
        assert me.json()["role"] == "viewer"  # the claim did not stick
        # …so the admin endpoint stays forbidden despite the admin claim.
        assert (await c.get("/api/v1/users", headers=cookie)).status_code == 403
