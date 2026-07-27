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
from eeper.api.recording_settings import is_recording_enabled, read_media_root
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
        # The root the running children are writing to. None until the first reconcile.
        self._media_root: str | None = None

    async def _desired(self) -> tuple[str, set[int]]:
        """Where to record, and which cameras should be recording right now.

        The camera set is empty when the household has switched recording off in Settings —
        that is how the toggle works: no container is started or stopped (the api is
        unprivileged and has no Docker socket), the recorder simply stops wanting any
        children and ``reconcile`` tears the existing ffmpeg processes down on its next tick.
        A missing settings row means enabled, so an upgrade records exactly as it did before.

        The root comes from the same row, so switching disks in Settings is picked up on the
        very next tick without a restart."""
        async with self._sessionmaker() as session:
            root = await read_media_root(session, self._settings)
            if not await is_recording_enabled(session):
                return root, set()
            result = await session.execute(select(Camera.id).where(Camera.enabled))
            return root, set(result.scalars().all())

    async def _spawn(self, camera_id: int, media_root: str) -> None:
        out_dir = seg_dir(media_root, camera_id)
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
        media_root, desired = await self._desired()
        now = time.monotonic()
        if self._media_root is not None and media_root != self._media_root:
            # The storage target changed. Every child holds an open output file under the
            # OLD root, so they must be restarted — ffmpeg can't be redirected in place.
            # Existing segments stay where they are and age out under that root's own
            # retention pass; already-promoted clips keep their absolute stored path.
            _log.info("storage target changed: %s -> %s", self._media_root, media_root)
            for camera_id in list(self._children):
                await self._stop_child(camera_id)
            self._backoff_until.clear()
        self._media_root = media_root
        # Reap children that exited (stream dropped / camera outage); back off before respawn.
        for camera_id, proc in list(self._children.items()):
            if proc.returncode is not None:
                self._children.pop(camera_id, None)
                self._backoff_until[camera_id] = now + _RESPAWN_BACKOFF_SECONDS
                _log.warning("recorder for camera %s exited (rc=%s)", camera_id, proc.returncode)
        # Stop recordings for cameras no longer wanted (disabled camera, or recording
        # switched off entirely).
        for camera_id in list(self._children):
            if camera_id not in desired:
                await self._stop_child(camera_id)
        if not desired:
            # Nothing should be recording. Drop any crash backoff so flipping the toggle
            # back on resumes immediately instead of waiting out a stale timer.
            self._backoff_until.clear()
        # Start recordings for newly-enabled cameras (respecting backoff).
        for camera_id in desired:
            if camera_id in self._children:
                continue
            if now < self._backoff_until.get(camera_id, 0.0):
                continue
            await self._spawn(camera_id, media_root)

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
