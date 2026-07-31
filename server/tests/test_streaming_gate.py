"""Presence-gated streaming: the camera stops only when we KNOW the crib is empty.

This feature switches a baby monitor's camera off, so its failure modes are asymmetric. A
camera that stays on unnecessarily costs some power. A camera that is off while the baby is
in the crib defeats the entire product. Every test here exists to pin that asymmetry: no
sensor, a stale sensor, an unreadable setting, a lapsed override — each keeps streaming.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eeper.api.models import Device, StreamGating, ThermalFeaturesReading
from eeper.api.stream_gating import read_gate
from tests.conftest import Harness

ADMIN = {"username": "streamadmin", "password": "correct horse battery staple"}


async def _sign_in_admin(api: Harness) -> None:
    r = await api.client.post("/api/v1/system/first-boot", json=ADMIN)
    assert r.status_code == 201, r.text


def _sessionmaker(api: Harness):  # type: ignore[no-untyped-def]
    engine = create_async_engine(api.settings.database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(
    api: Harness,
    *,
    kind: str | None = "thermal",
    presence: bool | None = None,
    age_seconds: float = 1.0,
    enabled: bool | None = None,
    override_until: datetime | None = None,
) -> None:
    """Put the world into a specific state: an optional presence device, an optional recent
    reading from it, and an optional gating row."""
    engine, sm = _sessionmaker(api)
    try:
        async with sm() as s:
            device_id = None
            if kind is not None:
                d = Device(name="thermal-crib", kind=kind, household_id="default")
                s.add(d)
                await s.flush()
                device_id = d.id
            if presence is not None and device_id is not None:
                s.add(
                    ThermalFeaturesReading(
                        ts=datetime.now(UTC) - timedelta(seconds=age_seconds),
                        household_id="default",
                        device_id=device_id,
                        presence=presence,
                        presence_confidence=0.9 if presence else 0.0,
                        warm_region_area=0.09 if presence else 0.0,
                    )
                )
            if enabled is not None:
                s.add(
                    StreamGating(
                        household_id="default", enabled=enabled, override_until=override_until
                    )
                )
            await s.commit()
    finally:
        await engine.dispose()


async def _gate(api: Harness):  # type: ignore[no-untyped-def]
    engine, sm = _sessionmaker(api)
    try:
        async with sm() as s:
            return await read_gate(s, "default")
    finally:
        await engine.dispose()


# ── the gate keeps streaming unless it is certain ────────────────────────────


async def test_a_missing_row_does_not_gate(api: Harness) -> None:
    """A feature that turns a camera off must never switch itself on during an upgrade."""
    await _sign_in_admin(api)
    gate = await _gate(api)
    assert gate.enabled is False
    assert gate.should_stream is True
    assert gate.reason == "disabled"


async def test_no_presence_input_keeps_streaming_even_when_enabled(api: Harness) -> None:
    """An operator can enable gating and then unpair the sensor. Gating on a signal that
    cannot arrive would leave the camera off forever."""
    await _sign_in_admin(api)
    await _seed(api, kind=None, enabled=True)
    gate = await _gate(api)
    assert gate.available is False
    assert gate.should_stream is True
    assert gate.reason == "unavailable"


async def test_a_stale_sensor_keeps_streaming(api: Harness) -> None:
    """The distinction the whole module turns on: a node that stopped reporting tells us
    NOTHING about the crib. Absent evidence is not evidence of absence."""
    await _sign_in_admin(api)
    await _seed(api, presence=False, age_seconds=600, enabled=True)
    gate = await _gate(api)
    assert gate.presence.stale is True
    assert gate.presence.present is None, "unknown, not absent"
    assert gate.should_stream is True
    assert gate.reason == "unknown"


async def test_a_working_sensor_reporting_empty_stops_the_stream(api: Harness) -> None:
    """The one case that actually gates."""
    await _sign_in_admin(api)
    await _seed(api, presence=False, enabled=True)
    gate = await _gate(api)
    assert gate.presence.known_absent is True
    assert gate.should_stream is False
    assert gate.reason == "no_presence"


async def test_presence_resumes_the_stream(api: Harness) -> None:
    await _sign_in_admin(api)
    await _seed(api, presence=True, enabled=True)
    gate = await _gate(api)
    assert gate.should_stream is True
    assert gate.reason == "streaming"


async def test_an_active_override_beats_an_empty_crib(api: Harness) -> None:
    await _sign_in_admin(api)
    await _seed(
        api,
        presence=False,
        enabled=True,
        override_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    gate = await _gate(api)
    assert gate.should_stream is True
    assert gate.reason == "override"


async def test_a_lapsed_override_stops_overriding(api: Harness) -> None:
    """An expiry rather than a flag, so a forgotten override cannot disable gating forever."""
    await _sign_in_admin(api)
    await _seed(
        api,
        presence=False,
        enabled=True,
        override_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    gate = await _gate(api)
    assert gate.should_stream is False
    assert gate.reason == "no_presence"


# ── the API ──────────────────────────────────────────────────────────────────


async def test_enabling_without_a_presence_input_is_refused(api: Harness) -> None:
    """Refuse rather than accept-and-ignore: a stored 'enabled' with nothing to detect
    presence would read as configured while doing nothing."""
    await _sign_in_admin(api)
    r = await api.client.patch("/api/v1/streaming/settings", json={"enabled": True})
    assert r.status_code == 409, r.text


async def test_admin_can_enable_when_an_input_exists(api: Harness) -> None:
    await _sign_in_admin(api)
    await _seed(api, presence=True)
    r = await api.client.patch("/api/v1/streaming/settings", json={"enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["streaming"] is True
    assert body["presence"]["present"] is True


async def test_status_explains_a_dark_screen(api: Harness) -> None:
    """The Live view needs a reason, not just a boolean, or it can only show a blank frame."""
    await _sign_in_admin(api)
    await _seed(api, presence=False, enabled=True)
    body = (await api.client.get("/api/v1/streaming/status")).json()
    assert body["streaming"] is False
    assert body["reason"] == "no_presence"
    assert body["presence"]["available"] is True


async def test_start_anyway_is_open_to_viewers(api: Harness) -> None:
    """A viewer staring at "no baby detected" must be able to look for themselves. Enabling
    the gating is the admin decision; overriding it for half an hour is not."""
    await _sign_in_admin(api)
    await _seed(api, presence=False, enabled=True)
    r = await api.client.post(
        "/api/v1/users", json={"username": "viewer9", "password": "x" * 12, "role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text
    async with api.fresh() as viewer:
        login = {"username": "viewer9", "password": "x" * 12}
        assert (await viewer.post("/api/v1/auth/login", json=login)).status_code == 200
        # ...but a viewer still may not change the household setting.
        assert (
            await viewer.patch("/api/v1/streaming/settings", json={"enabled": False})
        ).status_code == 403
        r = await viewer.post("/api/v1/streaming/override", json={"minutes": 5})
        assert r.status_code == 200, r.text
        assert r.json()["streaming"] is True
        assert r.json()["reason"] == "override"


async def test_overriding_when_gating_is_off_is_refused(api: Harness) -> None:
    await _sign_in_admin(api)
    r = await api.client.post("/api/v1/streaming/override", json={"minutes": 5})
    assert r.status_code == 409, r.text


async def test_disabling_the_gate_clears_a_stale_override(api: Harness) -> None:
    """The override exists to escape the gate; leaving the expiry behind would silently
    suppress the next time gating is switched on."""
    await _sign_in_admin(api)
    await _seed(api, presence=False, enabled=True)
    await api.client.post("/api/v1/streaming/override", json={"minutes": 60})
    off = (await api.client.patch("/api/v1/streaming/settings", json={"enabled": False})).json()
    assert off["override_until"] is None
    back_on = (await api.client.patch("/api/v1/streaming/settings", json={"enabled": True})).json()
    assert back_on["streaming"] is False, "the empty crib must gate again immediately"
    assert back_on["reason"] == "no_presence"


async def test_the_mic_stops_with_the_camera(api: Harness) -> None:
    """ "Stop the stream" that leaves a live microphone running is not what it says. The mic
    was previously exempted on the reasoning that listening is nearly free next to video —
    but an always-on nursery mic is a privacy question, not only a power one."""
    from unittest.mock import AsyncMock

    from eeper.api.camera_monitor import CameraMonitor

    await _sign_in_admin(api)
    await _seed(api, presence=False, enabled=True)  # working sensor, empty crib

    engine, sm = _sessionmaker(api)
    try:
        gateway = AsyncMock()
        gateway.stream_names.return_value = {"cam1", "mic"}
        settings = api.settings
        settings.audio_source_url = "rtsp://audio:8554/mic"
        monitor = CameraMonitor(gateway, sm, settings)
        await monitor.reconcile()
    finally:
        await engine.dispose()

    removed = {c.args[0] for c in gateway.remove_stream.await_args_list}
    assert "mic" in removed, "the room mic must stop when the stream is stopped"
    assert gateway.add_stream.await_count == 0, "nothing should be re-registered while gated"
