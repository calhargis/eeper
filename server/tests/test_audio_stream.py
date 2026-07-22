"""Host-microphone (USB/ALSA) integration via the audio adapter.

The api merges the mic's Opus stream into every camera's go2rtc stream (so a
camera with no native audio still plays listen-in AND feeds the sound nudge),
registers it standalone as ``mic`` for camera-independent room-listen, and reports
its availability. Camera-native audio still works when no mic is configured.
"""

from __future__ import annotations

from typing import cast

from eeper.api.camera_monitor import CameraMonitor
from eeper.api.config import Settings
from eeper.api.gateway import Go2rtcClient
from eeper.api.models import Camera
from tests.conftest import Harness

MIC = "rtsp://eeper-audio-adapter:8554/mic"
ADMIN_USER, ADMIN_PW = "admin", "correct horse battery staple"


class _FakeGateway:
    """Captures the streams the monitor registers (mirrors Go2rtcClient's surface)."""

    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.streams: dict[str, list[str]] = {}
        self._existing: set[str] = set(existing)

    async def add_stream(self, name: str, sources: list[str]) -> None:
        self.streams[name] = sources
        self._existing.add(name)

    async def stream_names(self) -> set[str]:
        return set(self._existing)


def _settings(**kw: object) -> Settings:
    return Settings(database_url="postgresql+asyncpg://x/y", secret_key="0" * 16, **kw)  # type: ignore[arg-type]


def _camera(has_audio: bool = False) -> Camera:
    return Camera(
        id=1,
        name="crib",
        source_url="rtsp://cam:8554/cam",
        codec="h264",
        width=1280,
        height=720,
        has_audio=has_audio,
        household_id="default",
    )


def _monitor(gw: _FakeGateway, settings: Settings) -> CameraMonitor:
    return CameraMonitor(cast(Go2rtcClient, gw), sessionmaker=None, settings=settings)  # type: ignore[arg-type]


# ── stream registration: mic merged into the camera stream ───────────────────


async def test_register_merges_the_mic_as_camera_audio() -> None:
    gw = _FakeGateway()
    mon = _monitor(gw, _settings(audio_source_url=MIC))
    await mon.register(_camera(has_audio=False))
    assert gw.streams["cam1"] == ["rtsp://cam:8554/cam", MIC]  # video + merged mic


async def test_register_prefers_the_mic_over_camera_native_audio() -> None:
    # A configured host mic wins over the camera's own audio transcode — never both,
    # since two audio tracks would race in go2rtc.
    gw = _FakeGateway()
    mon = _monitor(gw, _settings(audio_source_url=MIC))
    await mon.register(_camera(has_audio=True))
    assert gw.streams["cam1"] == ["rtsp://cam:8554/cam", MIC]
    assert "ffmpeg:cam1#audio=opus" not in gw.streams["cam1"]


async def test_register_without_mic_keeps_camera_audio_transcode() -> None:
    gw = _FakeGateway()
    mon = _monitor(gw, _settings())  # no mic
    await mon.register(_camera(has_audio=True))
    assert gw.streams["cam1"] == ["rtsp://cam:8554/cam", "ffmpeg:cam1#audio=opus"]


async def test_register_video_only_without_mic_has_no_audio_source() -> None:
    gw = _FakeGateway()
    mon = _monitor(gw, _settings())
    await mon.register(_camera(has_audio=False))
    assert gw.streams["cam1"] == ["rtsp://cam:8554/cam"]


# ── effective audio + standalone mic stream ──────────────────────────────────


async def test_effective_has_audio_reflects_the_merged_mic() -> None:
    gw = _FakeGateway()
    with_mic = _monitor(gw, _settings(audio_source_url=MIC))
    without = _monitor(gw, _settings())
    assert with_mic.mic_available is True
    assert with_mic.effective_has_audio(_camera(has_audio=False)) is True
    assert without.mic_available is False
    assert without.effective_has_audio(_camera(has_audio=False)) is False
    assert without.effective_has_audio(_camera(has_audio=True)) is True


async def test_reconcile_registers_the_standalone_mic_stream() -> None:
    gw = _FakeGateway()
    mon = _monitor(gw, _settings(audio_source_url=MIC))

    async def _no_cameras() -> list[Camera]:
        return []

    mon._enabled_cameras = _no_cameras  # type: ignore[method-assign]
    await mon.reconcile()
    assert gw.streams["mic"] == [MIC]


async def test_reconcile_without_mic_registers_no_mic_stream() -> None:
    gw = _FakeGateway()
    mon = _monitor(gw, _settings())

    async def _no_cameras() -> list[Camera]:
        return []

    mon._enabled_cameras = _no_cameras  # type: ignore[method-assign]
    await mon.reconcile()
    assert "mic" not in gw.streams


# ── the /audio endpoint (availability + auth + no-mic guard) ──────────────────


async def _first_boot(api: Harness) -> None:
    r = await api.client.post(
        "/api/v1/system/first-boot", json={"username": ADMIN_USER, "password": ADMIN_PW}
    )
    assert r.status_code in (200, 201), r.text


async def test_audio_status_reports_availability(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.get("/api/v1/audio")
    assert r.status_code == 200, r.text
    assert r.json() == {"available": False}  # no mic configured by default
    # Configure a mic (mutate the settings the app reads) — availability flips.
    api.settings.audio_source_url = MIC
    r2 = await api.client.get("/api/v1/audio")
    assert r2.json() == {"available": True}


async def test_audio_requires_auth(api: Harness) -> None:
    async with api.fresh() as anon:
        assert (await anon.get("/api/v1/audio")).status_code == 401


async def test_audio_webrtc_404_when_no_mic(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.post(
        "/api/v1/audio/webrtc", content="v=0", headers={"content-type": "application/sdp"}
    )
    assert r.status_code == 404, r.text  # guarded before the gateway is ever touched
