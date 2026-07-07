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

    async def register(self, camera: Camera) -> None:
        await self._gateway.add_stream(stream_name(camera.id), camera.source_url)

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
        """Re-register any enabled camera whose stream is missing from go2rtc."""
        try:
            existing = await self._gateway.stream_names()
        except GatewayError:
            existing = set()
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
