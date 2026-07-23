"""In-process auth matrix: login, refresh rotation + reuse detection, logout
revocation, TOTP enroll/challenge, expired-token rejection, role denial, API
tokens, and brute-force lockout with clock control. Runs against a real Postgres
(see conftest.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pyotp

from eeper.api.tokens import create_access_token
from tests.conftest import Harness

ADMIN_USER = "admin"
ADMIN_PW = "correct horse battery staple"


async def _first_boot(api: Harness) -> None:
    r = await api.client.post(
        "/api/v1/system/first-boot",
        json={"username": ADMIN_USER, "password": ADMIN_PW},
    )
    assert r.status_code == 201, r.text


async def test_first_boot_logs_admin_in(api: Harness) -> None:
    await _first_boot(api)
    me = await api.client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


async def test_login_wrong_then_right(api: Harness) -> None:
    await _first_boot(api)
    async with api.fresh() as c:
        bad = await c.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": "nope"})
        assert bad.status_code == 401
        good = await c.post(
            "/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PW}
        )
        assert good.status_code == 200
        assert good.json()["totp_required"] is False
        assert (await c.get("/api/v1/me")).status_code == 200


async def test_refresh_rotation_issues_new_token(api: Harness) -> None:
    await _first_boot(api)
    old_refresh = api.client.cookies.get("eeper_refresh")
    assert old_refresh
    r = await api.client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new_refresh = api.client.cookies.get("eeper_refresh")
    assert new_refresh and new_refresh != old_refresh
    assert (await api.client.get("/api/v1/me")).status_code == 200


async def test_refresh_reuse_revokes_family(api: Harness) -> None:
    await _first_boot(api)
    old_refresh = api.client.cookies.get("eeper_refresh")
    assert old_refresh
    # Rotate once — api.client now holds a new refresh; old_refresh is spent.
    assert (await api.client.post("/api/v1/auth/refresh")).status_code == 200

    async with api.fresh() as c:
        # Send the spent token via an explicit Cookie header (robust against
        # cookie-jar domain matching).
        reuse = await c.post(
            "/api/v1/auth/refresh", headers={"Cookie": f"eeper_refresh={old_refresh}"}
        )
        assert reuse.status_code == 401  # replay of a rotated token is detected

    # The whole family is revoked, so even the current (rotated) token is dead.
    assert (await api.client.post("/api/v1/auth/refresh")).status_code == 401


async def test_logout_revokes_refresh(api: Harness) -> None:
    await _first_boot(api)
    old_refresh = api.client.cookies.get("eeper_refresh")
    assert old_refresh
    assert (await api.client.post("/api/v1/auth/logout")).status_code == 200
    # Prove SERVER-SIDE revocation by replaying the captured token explicitly,
    # not just that the client dropped its cookie.
    async with api.fresh() as c:
        replayed = await c.post(
            "/api/v1/auth/refresh", headers={"Cookie": f"eeper_refresh={old_refresh}"}
        )
        assert replayed.status_code == 401


async def test_refresh_concurrent_single_winner(api: Harness) -> None:
    await _first_boot(api)
    refresh = api.client.cookies.get("eeper_refresh")
    assert refresh

    async def attempt() -> int:
        async with api.fresh() as c:
            r = await c.post("/api/v1/auth/refresh", headers={"Cookie": f"eeper_refresh={refresh}"})
            return r.status_code

    # Two rotations of the SAME token: exactly one wins; the other is reuse (401).
    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == [200, 401], results


async def test_expired_access_token_rejected(api: Harness) -> None:
    await _first_boot(api)
    expired = create_access_token(
        api.settings.secret_key,
        user_id=1,
        role="admin",
        now=datetime.now(UTC) - timedelta(hours=1),
        ttl_seconds=60,
    )
    async with api.fresh() as c:
        r = await c.get("/api/v1/me", headers={"Cookie": f"eeper_access={expired}"})
        assert r.status_code == 401


async def test_totp_enroll_activate_and_challenge_login(api: Harness) -> None:
    await _first_boot(api)
    enroll = await api.client.post("/api/v1/auth/totp/enroll")
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert enroll.json()["provisioning_uri"].startswith("otpauth://")

    activate = await api.client.post(
        "/api/v1/auth/totp/activate", json={"code": pyotp.TOTP(secret).now()}
    )
    assert activate.status_code == 200

    async with api.fresh() as c:
        login = await c.post(
            "/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PW}
        )
        assert login.status_code == 200
        assert login.json()["totp_required"] is True
        challenge = login.json()["challenge"]
        assert (await c.get("/api/v1/me")).status_code == 401  # not authed until TOTP

        bad = await c.post(
            "/api/v1/auth/totp/verify", json={"challenge": challenge, "code": "000000"}
        )
        assert bad.status_code == 401

        good = await c.post(
            "/api/v1/auth/totp/verify",
            json={"challenge": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert good.status_code == 200
        assert (await c.get("/api/v1/me")).status_code == 200


async def test_totp_verify_is_rate_limited(api: Harness) -> None:
    await _first_boot(api)  # max_failed_logins=3 (see conftest)
    secret = (await api.client.post("/api/v1/auth/totp/enroll")).json()["secret"]
    assert (
        await api.client.post("/api/v1/auth/totp/activate", json={"code": pyotp.TOTP(secret).now()})
    ).status_code == 200

    async with api.fresh() as c:
        challenge = (
            await c.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PW})
        ).json()["challenge"]
        for _ in range(3):
            r = await c.post(
                "/api/v1/auth/totp/verify", json={"challenge": challenge, "code": "000000"}
            )
            assert r.status_code == 401
        # Locked now — even a VALID code is refused (the 2FA step is rate-limited).
        blocked = await c.post(
            "/api/v1/auth/totp/verify",
            json={"challenge": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert blocked.status_code == 429


async def test_viewer_denied_on_admin_endpoints(api: Harness) -> None:
    await _first_boot(api)
    created = await api.client.post(
        "/api/v1/users",
        json={"username": "grandpa", "password": "viewer-password-123", "role": "viewer"},
    )
    assert created.status_code == 201

    async with api.fresh() as viewer:
        assert (
            await viewer.post(
                "/api/v1/auth/login",
                json={"username": "grandpa", "password": "viewer-password-123"},
            )
        ).status_code == 200
        assert (await viewer.get("/api/v1/me")).status_code == 200  # can see self
        # Admin-only endpoints are forbidden for a viewer.
        assert (await viewer.get("/api/v1/users")).status_code == 403
        assert (await viewer.post("/api/v1/tokens", json={"name": "t"})).status_code == 403


async def test_admin_endpoints_require_auth(api: Harness) -> None:
    await _first_boot(api)
    async with api.fresh() as anon:
        assert (await anon.get("/api/v1/users")).status_code == 401
        assert (await anon.get("/api/v1/me")).status_code == 401


async def test_brute_force_lockout_with_clock_control(api: Harness) -> None:
    await _first_boot(api)  # max_failed_logins=3, lockout_seconds=300 (see conftest)
    async with api.fresh() as c:
        for _ in range(3):
            r = await c.post(
                "/api/v1/auth/login", json={"username": ADMIN_USER, "password": "wrong"}
            )
            assert r.status_code == 401
        # Locked now — even the correct password is refused (generic 401, no
        # 429-vs-401 oracle that would reveal the account exists).
        locked = await c.post(
            "/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PW}
        )
        assert locked.status_code == 401

        # Advance the clock past the lockout window; login works again.
        api.clock["now"] = api.clock["now"] + timedelta(seconds=301)
        ok = await c.post("/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PW})
        assert ok.status_code == 200


async def test_api_token_scopes_are_enforced(api: Harness) -> None:
    await _first_boot(api)
    read_token = (
        await api.client.post("/api/v1/tokens", json={"name": "ha", "scopes": ["read"]})
    ).json()["token"]
    admin_token = (
        await api.client.post("/api/v1/tokens", json={"name": "adm", "scopes": ["admin"]})
    ).json()["token"]

    async with api.fresh() as c:  # read-scoped token: no admin access
        h = {"Authorization": f"Bearer {read_token}"}
        assert (await c.get("/api/v1/me", headers=h)).status_code == 200
        assert (await c.get("/api/v1/users", headers=h)).status_code == 403
        assert (
            await c.post(
                "/api/v1/users",
                headers=h,
                json={"username": "x", "password": "a-strong-password-1", "role": "admin"},
            )
        ).status_code == 403

    async with api.fresh() as c:  # admin-scoped token: admin access allowed
        h = {"Authorization": f"Bearer {admin_token}"}
        assert (await c.get("/api/v1/users", headers=h)).status_code == 200


async def test_api_token_revoke(api: Harness) -> None:
    await _first_boot(api)
    created = (
        await api.client.post("/api/v1/tokens", json={"name": "t", "scopes": ["read"]})
    ).json()
    assert created["scopes"] == ["read"]
    async with api.fresh() as c:
        h = {"Authorization": f"Bearer {created['token']}"}
        assert (await c.get("/api/v1/me", headers=h)).status_code == 200

    assert (await api.client.delete(f"/api/v1/tokens/{created['id']}")).status_code == 200

    async with api.fresh() as c:
        h = {"Authorization": f"Bearer {created['token']}"}
        assert (await c.get("/api/v1/me", headers=h)).status_code == 401  # revoked


async def test_remember_me_controls_cookie_persistence(api: Harness) -> None:
    await _first_boot(api)

    def refresh_attrs(resp: httpx.Response) -> str:
        for c in resp.headers.get_list("set-cookie"):
            if c.startswith("eeper_refresh="):
                return c.lower()
        return ""

    def all_cookies(resp: httpx.Response) -> str:
        return " ".join(resp.headers.get_list("set-cookie")).lower()

    creds = {"username": ADMIN_USER, "password": ADMIN_PW}

    # remember=True → persistent refresh cookie (has Max-Age) + marker "1"
    r = await api.client.post("/api/v1/auth/login", json={**creds, "remember": True})
    assert r.status_code == 200
    assert "max-age" in refresh_attrs(r)
    assert "eeper_persist=1" in all_cookies(r)

    # remember=False → session cookie (no Max-Age) + marker "0"
    r = await api.client.post("/api/v1/auth/login", json={**creds, "remember": False})
    assert r.status_code == 200
    assert "max-age" not in refresh_attrs(r)
    assert "eeper_persist=0" in all_cookies(r)

    # a token refresh preserves the session-only choice (via the persist marker cookie)
    r = await api.client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    assert "max-age" not in refresh_attrs(r)

    # the default (no field) stays persistent — backward-compatible with older clients
    r = await api.client.post("/api/v1/auth/login", json=creds)
    assert r.status_code == 200
    assert "max-age" in refresh_attrs(r)
