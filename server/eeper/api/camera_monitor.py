"""Background camera health + gateway reconciliation.

A single asyncio task periodically probes each enabled camera's source directly
(go2rtc's own re-serve buffers/reconnects on demand and lags a real outage) for
liveness, and re-registers any streams missing from go2rtc (e.g. after a gateway
restart). Probes run concurrently so one hung source can't starve the others.
Health lives in memory; the api runs a single worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eeper.api.config import Settings
from eeper.api.gateway import GatewayError, Go2rtcClient
from eeper.api.models import Camera
from eeper.api.probe import ProbeError, probe_video

_log = logging.getLogger("eeper.camera_monitor")


@dataclass(frozen=True)
class CameraHealth:
    online: bool
    last_checked: datetime


def stream_name(camera_id: int) -> str:
    return f"cam{camera_id}"


class CameraMonitor:
    def __init__(
        self,
        gateway: Go2rtcClient,
        sessionmaker: async_sessionmaker,  # type: ignore[type-arg]
        settings: Settings,
    ) -> None:
        self._gateway = gateway
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._health: dict[int, CameraHealth] = {}
        self._in_flight: set[int] = set()
        self._task: asyncio.Task[None] | None = None

    def get_health(self, camera_id: int) -> CameraHealth | None:
        return self._health.get(camera_id)

    def forget(self, camera_id: int) -> None:
        self._health.pop(camera_id, None)

    @property
    def mic_available(self) -> bool:
        """Whether a host microphone is configured (the audio adapter's stream)."""
        return bool(self._settings.audio_source_url)

    def effective_has_audio(self, camera: Camera) -> bool:
        """A camera plays audio if its own source carries a track OR a host mic is
        merged into every camera stream. This is what lights up the listen-in
        control and the sustained-sound nudge, so it must reflect the merge."""
        return camera.has_audio or self.mic_available

    async def register(self, camera: Camera) -> None:
        name = stream_name(camera.id)
        # Raw RTSP source first (serves H.264 + AAC to the recorder / audio
        # extractor). Audio for the browser comes from ONE of two mutually
        # exclusive sources (never both — two audio tracks would race in go2rtc):
        #   * a host mic (the audio adapter) merged in as the camera's audio, when
        #     EEPER_AUDIO_SOURCE_URL is set — already Opus, so WebRTC-ready with no
        #     transcode, and it also feeds the insight sound nudge; else
        #   * an on-demand ffmpeg source transcoding the source's own audio to Opus
        #     (AAC isn't a WebRTC codec), only when the source itself has audio.
        # Either way the audio source is deliberately audio-only: go2rtc serves the
        # WebRTC video from source 0 (raw copy), so the first frame never waits on
        # the audio to spin up.
        sources = [camera.source_url]
        if self._settings.audio_source_url:
            sources.append(self._settings.audio_source_url)
        elif camera.has_audio:
            sources.append(f"ffmpeg:{name}#audio=opus")
        await self._gateway.add_stream(name, sources)

    async def _enabled_cameras(self) -> list[Camera]:
        async with self._sessionmaker() as session:
            result = await session.execute(select(Camera).where(Camera.enabled))
            return list(result.scalars().all())

    async def probe(self, camera: Camera) -> bool:
        """Probe the camera SOURCE directly for accurate liveness (go2rtc's own
        re-serve buffers/reconnects on-demand and lags a real source outage)."""
        camera_id = camera.id
        if camera_id in self._in_flight:
            current = self._health.get(camera_id)
            return current.online if current else False
        self._in_flight.add(camera_id)
        try:
            try:
                await probe_video(camera.source_url, self._settings.probe_timeout_seconds)
                online = True
            except ProbeError:
                online = False
            self._health[camera_id] = CameraHealth(online=online, last_checked=datetime.now(UTC))
            return online
        finally:
            self._in_flight.discard(camera_id)

    async def reconcile(self) -> None:
        """Re-register any enabled camera whose stream is missing from go2rtc, and
        (re)register the standalone host-mic stream for camera-independent listen-in."""
        try:
            existing = await self._gateway.stream_names()
        except GatewayError:
            existing = set()
        mic = self._settings.mic_stream_name
        if self._settings.audio_source_url and mic not in existing:
            try:
                await self._gateway.add_stream(mic, [self._settings.audio_source_url])
            except GatewayError:
                _log.warning("could not register the host mic stream in gateway")
        for camera in await self._enabled_cameras():
            if stream_name(camera.id) not in existing:
                try:
                    await self.register(camera)
                except GatewayError:
                    _log.warning("could not register camera %s in gateway", camera.id)

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile()
                cameras = await self._enabled_cameras()
                # Drop health for cameras that are gone/disabled so a deleted
                # camera (or one whose probe raced its delete) can't leave a
                # stale entry that never gets pruned.
                live_ids = {c.id for c in cameras}
                for stale_id in self._health.keys() - live_ids:
                    self._health.pop(stale_id, None)
                # Probe concurrently: a single hung source must not delay the
                # health/recovery of every other camera.
                if cameras:
                    await asyncio.gather(*(self.probe(c) for c in cameras), return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # a monitor tick must never kill the loop
                _log.exception("camera monitor tick failed")
            await asyncio.sleep(self._settings.health_interval_seconds)

    async def start(self) -> None:
        await self.reconcile()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
