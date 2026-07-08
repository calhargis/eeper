"""Supervises one ffmpeg audio-decode child per enabled camera.

Mirrors the recorder's supervisor (reconcile ``cameras WHERE enabled``, spawn on
enable, SIGTERM->SIGKILL on disable, respawn-with-backoff on exit) — but here the
child's stdout IS the product: a per-child reader drains the PCM pipe into a
window ring (in-memory, cheap) and a separate slow loop writes the newest window
to the test tap (decoupled from the drain). Because the reader is the ffmpeg
child's SOLE stdout consumer, a stopped reader would leave the pipe undrained and
wedge the still-alive child, so reconcile reaps a child whose reader task has
ended (not only one whose returncode is set). M2.2 feature extractors will read
the same rings in-process.
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
from eeper.insight.audio import decode_command
from eeper.insight.tap import WavTap
from eeper.insight.window import SPEC, WindowRing

_log = logging.getLogger("eeper.insight.supervisor")
_RESPAWN_BACKOFF_SECONDS = 5.0
_STOP_GRACE_SECONDS = 5.0
_TAP_FLUSH_SECONDS = 0.5
_READ_CHUNK = 65536


class AudioSupervisor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._spec = SPEC
        self._tap = WavTap(settings.insight_tap_dir, self._spec)
        self._children: dict[int, Process] = {}
        self._readers: dict[int, asyncio.Task[None]] = {}
        self._rings: dict[int, WindowRing] = {}
        self._backoff_until: dict[int, float] = {}

    async def _enabled_camera_ids(self) -> set[int]:
        async with self._sessionmaker() as session:
            result = await session.execute(select(Camera.id).where(Camera.enabled))
            return set(result.scalars().all())

    async def _spawn(self, camera_id: int) -> None:
        rtsp_url = f"{self._settings.go2rtc_rtsp_url.rstrip('/')}/cam{camera_id}"
        cmd = decode_command(rtsp_url, self._spec)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        ring = WindowRing(self._spec)
        self._children[camera_id] = proc
        self._rings[camera_id] = ring
        self._readers[camera_id] = asyncio.create_task(self._drain(camera_id, proc, ring))
        _log.info("extracting audio from camera %s", camera_id)

    async def _drain(self, camera_id: int, proc: Process, ring: WindowRing) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            while True:
                data = await stdout.read(_READ_CHUNK)
                if not data:
                    return  # ffmpeg closed stdout (exited)
                ring.feed(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # a drain failure must not take down the supervisor
            _log.exception("audio drain for camera %s failed", camera_id)

    async def _stop_child(self, camera_id: int) -> None:
        reader = self._readers.pop(camera_id, None)
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
        self._rings.pop(camera_id, None)
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
        _log.info("stopped audio extraction for camera %s", camera_id)

    async def reconcile(self) -> None:
        desired = await self._enabled_camera_ids()
        now = time.monotonic()
        for camera_id, proc in list(self._children.items()):
            reader = self._readers.get(camera_id)
            reader_done = reader is not None and reader.done()
            # Reap a child that exited OR whose reader task ended: a clean EOF, but
            # also an unexpected drain error — which would otherwise leave a live,
            # write-blocked ffmpeg (returncode stays None) that the returncode check
            # never reaps, silently stalling that camera's audio.
            if proc.returncode is not None or reader_done:
                await self._stop_child(camera_id)
                self._backoff_until[camera_id] = now + _RESPAWN_BACKOFF_SECONDS
                _log.warning(
                    "audio extractor for camera %s stopped (rc=%s, reader_done=%s)",
                    camera_id,
                    proc.returncode,
                    reader_done,
                )
        for camera_id in list(self._children):
            if camera_id not in desired:
                await self._stop_child(camera_id)
        for camera_id in desired:
            if camera_id in self._children:
                continue
            if now < self._backoff_until.get(camera_id, 0.0):
                continue
            await self._spawn(camera_id)

    def _flush_taps(self) -> None:
        if not self._tap.enabled:
            return
        for camera_id, ring in list(self._rings.items()):
            if ring.windows:
                self._tap.write(camera_id, ring.windows[-1])

    async def _tap_loop(self) -> None:
        while True:
            try:
                self._flush_taps()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("tap flush failed")
            await asyncio.sleep(_TAP_FLUSH_SECONDS)

    async def run(self) -> None:
        tap_task = asyncio.create_task(self._tap_loop())
        try:
            while True:
                try:
                    await self.reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception:  # a tick must never kill the supervisor
                    _log.exception("insight reconcile tick failed")
                await asyncio.sleep(self._settings.health_interval_seconds)
        finally:
            tap_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tap_task
            await self.stop()

    async def stop(self) -> None:
        for camera_id in list(self._children):
            await self._stop_child(camera_id)
