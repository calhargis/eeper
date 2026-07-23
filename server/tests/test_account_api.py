"""Change-password endpoint (POST /me/password): re-authentication, the length
policy, and session revocation (a password change logs out every other device)."""

from __future__ import annotations

from tests.conftest import Harness

ADMIN, PW = "admin", "correct horse battery staple"
NEWPW = "a-brand-new-passphrase-9"  # >= 12 chars, differs from PW


async def _first_boot(api: Harness) -> None:
    r = await api.client.post("/api/v1/system/first-boot", json={"username": ADMIN, "password": PW})
    assert r.status_code in (200, 201), r.text


async def _login(client, password: str, remember: bool = True):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN, "password": password, "remember": remember},
    )


def _set_cookies(resp) -> str:  # type: ignore[no-untyped-def]
    return " ".join(resp.headers.get_list("set-cookie"))


async def test_change_password_happy_path(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": PW, "new_password": NEWPW}
    )
    assert r.status_code == 200, r.text
    # The new password logs in; the old one no longer does.
    async with api.fresh() as c:
        assert (await _login(c, NEWPW)).status_code == 200
    async with api.fresh() as c:
        assert (await _login(c, PW)).status_code == 401


async def test_wrong_current_password_rejected(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": "not-it", "new_password": NEWPW}
    )
    assert r.status_code == 403, r.text
    # Unchanged: the original password still works.
    async with api.fresh() as c:
        assert (await _login(c, PW)).status_code == 200


async def test_short_new_password_rejected(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": PW, "new_password": "short"}
    )
    assert r.status_code == 422, r.text
    async with api.fresh() as c:
        assert (await _login(c, PW)).status_code == 200  # unchanged


async def test_same_new_password_rejected(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": PW, "new_password": PW}
    )
    assert r.status_code == 422, r.text


async def test_change_password_requires_auth(api: Harness) -> None:
    await _first_boot(api)
    async with api.fresh() as anon:
        r = await anon.post(
            "/api/v1/me/password", json={"current_password": PW, "new_password": NEWPW}
        )
        assert r.status_code == 401


async def test_change_password_revokes_other_sessions(api: Harness) -> None:
    await _first_boot(api)  # api.client == session A (logged in by first-boot)
    async with api.fresh() as b:  # session B: a second signed-in device
        assert (await _login(b, PW)).status_code == 200
        assert (await b.post("/api/v1/auth/refresh")).status_code == 200  # B is valid now
        # Session A changes the password.
        r = await api.client.post(
            "/api/v1/me/password", json={"current_password": PW, "new_password": NEWPW}
        )
        assert r.status_code == 200, r.text
        # B's refresh family is revoked → it can no longer refresh (logged out).
        assert (await b.post("/api/v1/auth/refresh")).status_code == 401
        # A stays signed in with a FRESH family — it can reach a protected route AND
        # rotate its own refresh (proving the new family is live, not just the 15-min JWT).
        assert (await api.client.get("/api/v1/me")).status_code == 200
        assert (await api.client.post("/api/v1/auth/refresh")).status_code == 200


async def test_change_password_preserves_session_only_choice(api: Harness) -> None:
    # A "remember me" OFF login uses session cookies; changing the password must NOT
    # silently upgrade it to a persistent 30-day session (the persist marker is now
    # "/"-scoped, so /me/password actually receives it).
    r = await api.client.post("/api/v1/system/first-boot", json={"username": ADMIN, "password": PW})
    assert r.status_code in (200, 201), r.text
    r = await _login(api.client, PW, remember=False)
    assert r.status_code == 200 and "eeper_persist=0" in _set_cookies(r)

    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": PW, "new_password": NEWPW}
    )
    assert r.status_code == 200, r.text
    cookies = _set_cookies(r)
    assert "eeper_persist=0" in cookies  # choice preserved
    # The re-issued refresh cookie stays session-scoped (no Max-Age).
    refresh_cookie = next(
        c for c in r.headers.get_list("set-cookie") if c.startswith("eeper_refresh=")
    )
    assert "max-age" not in refresh_cookie.lower(), refresh_cookie


async def test_change_password_locks_after_repeated_wrong_current(api: Harness) -> None:
    await _first_boot(api)
    # The harness sets max_failed_logins=3; wrong current-password attempts trip the
    # SAME account lockout as login.
    for _ in range(3):
        r = await api.client.post(
            "/api/v1/me/password", json={"current_password": "nope", "new_password": NEWPW}
        )
        assert r.status_code == 403
    # Locked: even the CORRECT current password is now refused (masked as 403)…
    r = await api.client.post(
        "/api/v1/me/password", json={"current_password": PW, "new_password": NEWPW}
    )
    assert r.status_code == 403, r.text
    # …and login is locked too (shared counter) — the right password still fails.
    async with api.fresh() as c:
        assert (await _login(c, PW)).status_code == 401
