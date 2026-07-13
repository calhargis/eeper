"""Pulse-ox gating tests (M4.2 slice 1): pulse-ox is inert unless the profile is enabled
AND an admin acknowledged the current disclaimer, acknowledgment is admin-only and
version-checked, and the disclaimer + accuracy caveat are served.
"""

from __future__ import annotations

from eeper.api.pulseox_copy import ACCURACY_CAVEAT, DISCLAIMER_VERSION

_PW = "correct horse battery staple"


async def _first_boot(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.client.post(
        "/api/v1/system/first-boot", json={"username": "admin", "password": _PW}
    )
    assert r.status_code == 201, r.text


async def _status(api) -> dict[str, object]:  # type: ignore[no-untyped-def]
    r = await api.client.get("/api/v1/pulseox/status")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_inert_when_profile_disabled(api) -> None:  # type: ignore[no-untyped-def]
    await _first_boot(api)
    api.settings.pulseox_profile_enabled = False
    s = await _status(api)
    assert s == {
        "profile_enabled": False,
        "acknowledged": False,
        "enabled": False,
        "disclaimer_version": DISCLAIMER_VERSION,
    }
    # Can't acknowledge when the deployment hasn't enabled the profile.
    r = await api.client.post("/api/v1/pulseox/acknowledge", json={"version": DISCLAIMER_VERSION})
    assert r.status_code == 409


async def test_gate_matrix_profile_on_needs_acknowledgment(api) -> None:  # type: ignore[no-untyped-def]
    await _first_boot(api)
    api.settings.pulseox_profile_enabled = True

    # Profile on but not acknowledged → still inert.
    assert (await _status(api))["enabled"] is False

    # Admin acknowledges the current version → enabled.
    ack = await api.client.post("/api/v1/pulseox/acknowledge", json={"version": DISCLAIMER_VERSION})
    assert ack.status_code == 200
    assert ack.json()["enabled"] is True
    assert (await _status(api))["enabled"] is True

    # Turning the profile back off makes it inert again (both halves are required).
    api.settings.pulseox_profile_enabled = False
    s = await _status(api)
    assert s["acknowledged"] is True and s["enabled"] is False


async def test_acknowledge_rejects_wrong_version(api) -> None:  # type: ignore[no-untyped-def]
    await _first_boot(api)
    api.settings.pulseox_profile_enabled = True
    r = await api.client.post("/api/v1/pulseox/acknowledge", json={"version": "not-the-version"})
    assert r.status_code == 409
    assert (await _status(api))["acknowledged"] is False


async def test_viewer_cannot_acknowledge(api) -> None:  # type: ignore[no-untyped-def]
    await _first_boot(api)
    api.settings.pulseox_profile_enabled = True
    created = await api.client.post(
        "/api/v1/users", json={"username": "grandparent", "password": _PW, "role": "viewer"}
    )
    assert created.status_code == 201, created.text
    async with api.fresh() as viewer:
        login = await viewer.post(
            "/api/v1/auth/login", json={"username": "grandparent", "password": _PW}
        )
        assert login.status_code == 200
        assert (await viewer.get("/api/v1/pulseox/status")).status_code == 200  # can view state
        r = await viewer.post("/api/v1/pulseox/acknowledge", json={"version": DISCLAIMER_VERSION})
        assert r.status_code == 403  # cannot acknowledge


async def test_disclaimer_served_with_caveat(api) -> None:  # type: ignore[no-untyped-def]
    await _first_boot(api)
    r = await api.client.get("/api/v1/pulseox/disclaimer")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == DISCLAIMER_VERSION
    assert body["accuracy_caveat"] == ACCURACY_CAVEAT
    assert "not a medical device" in body["text"].lower()
    assert body["safe_sleep_url"].startswith("https://")


async def test_status_requires_auth(api) -> None:  # type: ignore[no-untyped-def]
    async with api.fresh() as anon:
        assert (await anon.get("/api/v1/pulseox/status")).status_code == 401
