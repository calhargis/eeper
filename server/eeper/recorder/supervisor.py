"""Supervises one ``ffmpeg -c copy`` recording child per enabled camera.

Mirrors the CameraMonitor reconcile pattern: poll ``cameras WHERE enabled`` and
diff against running children — spawn on enable, stop (SIGTERM→SIGKILL) on
disable/delete, respawn-with-backoff on an unexpected exit (camera outage). The
in-process child dict is the single-writer-per-camera invariant that keeps
"newest sibling = active segment" true; the container boundary means a crash
can't leave an orphan ffmpeg behind (its children die with it).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from asyncio.subprocess import Process

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.config import Settings
from eeper.api.models import Camera
from eeper.recorder.layout import seg_dir
from eeper.recorder.record import segment_command

_log = logging.getLogger("eeper.recorder.supervisor")
_RESPAWN_BACKOFF_SECONDS = 5.0
_STOP_GRACE_SECONDS = 5.0


class RecorderSupervisor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._children: dict[int, Process] = {}
        self._backoff_until: dict[int, float] = {}

    async def _enabled_camera_ids(self) -> set[int]:
        async with self._sessionmaker() as session:
            result = await session.execute(select(Camera.id).where(Camera.enabled))
            return set(result.scalars().all())

    async def _spawn(self, camera_id: int) -> None:
        out_dir = seg_dir(self._settings.media_root, camera_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        rtsp_url = f"{self._settings.go2rtc_rtsp_url.rstrip('/')}/cam{camera_id}"
        cmd = segment_command(rtsp_url, out_dir, self._settings.segment_seconds)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._children[camera_id] = proc
        _log.info("recording camera %s -> %s", camera_id, out_dir)

    async def _stop_child(self, camera_id: int) -> None:
        proc = self._children.pop(camera_id, None)
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SECONDS)
        if proc.returncode is None:
            proc.kill()
            with contextlib.suppress(BaseException):
                await proc.wait()
        _log.info("stopped recording camera %s", camera_id)

    async def reconcile(self) -> None:
        desired = await self._enabled_camera_ids()
        now = time.monotonic()
        # Reap children that exited (stream dropped / camera outage); back off before respawn.
        for camera_id, proc in list(self._children.items()):
            if proc.returncode is not None:
                self._children.pop(camera_id, None)
                self._backoff_until[camera_id] = now + _RESPAWN_BACKOFF_SECONDS
                _log.warning("recorder for camera %s exited (rc=%s)", camera_id, proc.returncode)
        # Stop recordings for cameras no longer enabled.
        for camera_id in list(self._children):
            if camera_id not in desired:
                await self._stop_child(camera_id)
        # Start recordings for newly-enabled cameras (respecting backoff).
        for camera_id in desired:
            if camera_id in self._children:
                continue
            if now < self._backoff_until.get(camera_id, 0.0):
                continue
            await self._spawn(camera_id)

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception:  # a tick must never kill the supervisor
                    _log.exception("recorder reconcile tick failed")
                await asyncio.sleep(self._settings.health_interval_seconds)
        finally:
            await self.stop()

    async def stop(self) -> None:
        for camera_id in list(self._children):
            await self._stop_child(camera_id)
