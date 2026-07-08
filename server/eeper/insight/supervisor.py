"""Supervises the per-camera insight decode children and the motion scorer.

Extends the M2.1 audio supervisor into a two-stream engine. For each enabled
camera it runs an ffmpeg VIDEO child (gray frames -> FrameRing -> motion scorer),
and — only when the source carries audio — an ffmpeg AUDIO child (16 kHz PCM ->
WindowRing -> WAV tap), mirroring the recorder's reconcile/backoff/SIGTERM->KILL
pattern. Each stream is reaped independently (a video hiccup never tears down a
healthy audio stream, and vice versa), so listen-in audio and motion insight fail
and recover on their own.

The motion scorer diffs consecutive gray frames, EWMA-smooths the score, runs a
low/medium/high hysteresis state machine, and on each transition writes
state_history + events (DB first) then publishes a movement-level event over MQTT.
Backpressure is the FrameRing itself: a slow scorer snapshots only the newest pair
and the ring drops the backlog, so memory stays bounded and the processed frame
stays fresh. Everything here is an awareness signal — movement level, never a
medical or vital-sign reading.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from asyncio.subprocess import Process
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.config import Settings
from eeper.api.models import Camera
from eeper.insight import audio, video
from eeper.insight.frame import FRAME_SPEC, FrameRing
from eeper.insight.motion import Ewma, MovementStateMachine, confidence_for, frame_diff_score
from eeper.insight.motion_tap import MotionTap
from eeper.insight.mqtt import MotionPublisher
from eeper.insight.registry import available_inputs, extractors_for
from eeper.insight.state_writer import StateWriter
from eeper.insight.tap import WavTap
from eeper.insight.window import SPEC, WindowRing

_log = logging.getLogger("eeper.insight.supervisor")
_RESPAWN_BACKOFF_SECONDS = 5.0
_STOP_GRACE_SECONDS = 5.0
_TAP_FLUSH_SECONDS = 0.5
_READ_CHUNK = 65536

# A ring/feed protocol shared by WindowRing and FrameRing (both take raw bytes).


@dataclass
class _Stream:
    """One ffmpeg decode child, its stdout drain reader, and its ring."""

    proc: Process
    reader: asyncio.Task[None]
    ring: WindowRing | FrameRing


def _active_extractor_names(has_audio: bool) -> list[str]:
    """The names of the extractors that are actually running for this camera
    (status "active" and inputs available). In M2.2 that is just motion, whether or
    not the camera has audio (the audio-event extractor is declared but lands later).
    """
    inputs = available_inputs(has_audio)
    return sorted(e.name for e in extractors_for(inputs) if e.status == "active")


class InsightSupervisor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        publisher: MotionPublisher | None = None,
        writer: StateWriter | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._frame_spec = FRAME_SPEC
        self._audio_spec = SPEC
        self._wav_tap = WavTap(settings.insight_tap_dir, self._audio_spec)
        self._motion_tap = MotionTap(settings.insight_tap_dir)
        self._publisher = publisher or MotionPublisher(
            settings.mqtt_host, settings.mqtt_port, settings.mqtt_node
        )
        self._writer = writer or StateWriter(sessionmaker)
        self._video: dict[int, _Stream] = {}
        self._audio: dict[int, _Stream] = {}
        self._scorers: dict[int, asyncio.Task[None]] = {}
        # (camera_id, "video"|"audio") -> monotonic time before which not to respawn.
        self._backoff: dict[tuple[int, str], float] = {}
        # The active extractor names per camera, surfaced for the registry/C5 checks.
        self.active_extractors: dict[int, frozenset[str]] = {}

    # ── camera discovery ──────────────────────────────────────────────────────

    async def _desired_cameras(self) -> dict[int, bool]:
        """Enabled cameras -> whether each carries audio."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Camera.id, Camera.has_audio).where(Camera.enabled)
            )
            return {row[0]: bool(row[1]) for row in result.all()}

    # ── spawning ──────────────────────────────────────────────────────────────

    def _rtsp_url(self, camera_id: int) -> str:
        return f"{self._settings.go2rtc_rtsp_url.rstrip('/')}/cam{camera_id}"

    async def _spawn_video(self, camera_id: int) -> None:
        cmd = video.frame_decode_command(self._rtsp_url(camera_id), self._frame_spec)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        ring = FrameRing(self._frame_spec)
        reader = asyncio.create_task(self._drain(camera_id, "video", proc, ring))
        self._video[camera_id] = _Stream(proc, reader, ring)
        self._scorers[camera_id] = asyncio.create_task(self._score_loop(camera_id, ring))
        _log.info("scoring motion for camera %s", camera_id)

    async def _spawn_audio(self, camera_id: int) -> None:
        cmd = audio.decode_command(self._rtsp_url(camera_id), self._audio_spec)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        ring = WindowRing(self._audio_spec)
        reader = asyncio.create_task(self._drain(camera_id, "audio", proc, ring))
        self._audio[camera_id] = _Stream(proc, reader, ring)
        _log.info("extracting audio from camera %s", camera_id)

    async def _drain(
        self, camera_id: int, kind: str, proc: Process, ring: WindowRing | FrameRing
    ) -> None:
        """Drain a decode child's stdout into its ring. This is the child's sole
        stdout consumer, so it must never block (the ring feed is O(bytes), never
        awaits) — otherwise pipe backpressure would wedge the still-alive child."""
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
            _log.exception("%s drain for camera %s failed", kind, camera_id)

    # ── scoring ───────────────────────────────────────────────────────────────

    async def _score_loop(self, camera_id: int, ring: FrameRing) -> None:
        """Diff the newest consecutive frame pair at the frame rate, smooth, and
        drive the movement-level state machine. Publishes a motion sample every
        tick; on a transition writes state_history+events (DB first) then publishes
        the state event. A slow scorer (insight_scorer_delay_ms) still snapshots the
        freshest pair, so the ring drops the backlog rather than queueing it."""
        ewma = Ewma()
        machine = MovementStateMachine()
        interval = 1.0 / self._frame_spec.fps
        delay = self._settings.insight_scorer_delay_ms / 1000.0
        frames_scored = 0
        while True:
            try:
                if delay:
                    await asyncio.sleep(delay)  # artificial slowdown (backpressure test)
                prev, cur, cur_index = ring.snapshot()
                if prev is not None and cur is not None:
                    frames_scored += 1
                    score = ewma.update(frame_diff_score(prev, cur))
                    confidence = confidence_for(score)
                    wall = datetime.now(UTC)
                    epoch = wall.timestamp()
                    contributing = sorted(self.active_extractors.get(camera_id, frozenset()))
                    self._publisher.publish_motion(camera_id, score, confidence, epoch)
                    before = machine.level
                    before_change = machine.last_change_monotonic
                    new_level = machine.update(score, time.monotonic())
                    if new_level is not None:
                        # Persist first (the source of truth); only publish + keep the
                        # in-memory transition if the durable write succeeded. On a
                        # failed/timed-out write, revert so a later tick re-attempts —
                        # the DB and the published state can never diverge.
                        written = await self._writer.write_movement_change(
                            camera_id=camera_id,
                            ts=wall,
                            level=new_level.value,
                            previous=before.value,
                            confidence=confidence,
                            contributing=contributing,
                        )
                        if written:
                            self._publisher.publish_state(
                                camera_id,
                                new_level.value,
                                before.value,
                                confidence,
                                contributing,
                                epoch,
                            )
                        else:
                            machine.revert(before, before_change)
                    if self._motion_tap.enabled:
                        # Freshness = how far behind the newest fed frame the scored
                        # frame is (the backlog the drop path shed), in seconds. The
                        # drop path keeps this ~0; a queueing regression (scoring a
                        # stale frame) would make it grow — so the C4 assertion can
                        # actually go red.
                        frames_behind = max(0, ring.frames_fed - 1 - cur_index)
                        freshness = frames_behind / self._frame_spec.fps
                        self._motion_tap.write(
                            camera_id,
                            {
                                "ts": epoch,
                                "score": round(score, 5),
                                "level": machine.level.value,
                                "frames_fed": ring.frames_fed,
                                "frames_scored": frames_scored,
                                "freshness_seconds": round(freshness, 3),
                                "contributing_inputs": contributing,
                            },
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # a scoring failure must not take down the supervisor
                _log.exception("scoring loop for camera %s failed", camera_id)
            await asyncio.sleep(interval)

    # ── teardown ──────────────────────────────────────────────────────────────

    async def _stop_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _stop_stream(self, streams: dict[int, _Stream], camera_id: int, kind: str) -> None:
        stream = streams.pop(camera_id, None)
        if kind == "video":
            await self._stop_task(self._scorers.pop(camera_id, None))
        if stream is None:
            return
        await self._stop_task(stream.reader)
        proc = stream.proc
        if proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SECONDS)
            if proc.returncode is None:
                proc.kill()
                with contextlib.suppress(BaseException):
                    await proc.wait()
        _log.info("stopped %s for camera %s", kind, camera_id)

    async def _stop_camera(self, camera_id: int) -> None:
        await self._stop_stream(self._video, camera_id, "video")
        await self._stop_stream(self._audio, camera_id, "audio")
        self.active_extractors.pop(camera_id, None)

    def _reap_dead(self, streams: dict[int, _Stream], kind: str, now: float) -> list[int]:
        """Return the ids of streams that exited (returncode set) or whose reader
        task ended (a clean EOF or a drain crash — which would otherwise leave a
        live, write-blocked ffmpeg the returncode check never reaps)."""
        dead: list[int] = []
        for camera_id, stream in streams.items():
            reader_done = stream.reader.done()
            if stream.proc.returncode is not None or reader_done:
                self._backoff[(camera_id, kind)] = now + _RESPAWN_BACKOFF_SECONDS
                _log.warning(
                    "%s for camera %s stopped (rc=%s, reader_done=%s)",
                    kind,
                    camera_id,
                    stream.proc.returncode,
                    reader_done,
                )
                dead.append(camera_id)
        return dead

    # ── reconcile ─────────────────────────────────────────────────────────────

    async def reconcile(self) -> None:
        desired = await self._desired_cameras()
        now = time.monotonic()
        # 1. Reap dead streams per-stream (a dead video stream does not touch audio).
        for camera_id in self._reap_dead(self._video, "video", now):
            await self._stop_stream(self._video, camera_id, "video")
        for camera_id in self._reap_dead(self._audio, "audio", now):
            await self._stop_stream(self._audio, camera_id, "audio")
        # 2. Stop streams for cameras that are gone / disabled / lost their audio.
        for camera_id in list(self._video):
            if camera_id not in desired:
                await self._stop_camera(camera_id)
        for camera_id in list(self._audio):
            if camera_id not in desired or not desired[camera_id]:
                await self._stop_stream(self._audio, camera_id, "audio")
        # 3. (Re)start desired streams, respecting per-stream backoff.
        for camera_id, has_audio in desired.items():
            self.active_extractors[camera_id] = frozenset(_active_extractor_names(has_audio))
            if camera_id not in self._video and now >= self._backoff.get((camera_id, "video"), 0.0):
                await self._spawn_video(camera_id)
            if (
                has_audio
                and camera_id not in self._audio
                and now >= self._backoff.get((camera_id, "audio"), 0.0)
            ):
                await self._spawn_audio(camera_id)

    # ── audio WAV tap (M2.1) ──────────────────────────────────────────────────

    def _flush_wav_taps(self) -> None:
        if not self._wav_tap.enabled:
            return
        for camera_id, stream in list(self._audio.items()):
            ring = stream.ring
            if isinstance(ring, WindowRing) and ring.windows:
                self._wav_tap.write(camera_id, ring.windows[-1])

    async def _tap_loop(self) -> None:
        while True:
            try:
                self._flush_wav_taps()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("wav tap flush failed")
            await asyncio.sleep(_TAP_FLUSH_SECONDS)

    # ── lifecycle ─────────────────────────────────────────────────────────────

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
            await self._stop_task(tap_task)
            await self.stop()

    async def stop(self) -> None:
        for camera_id in list(self._video) + list(self._audio):
            await self._stop_camera(camera_id)
        self._publisher.close()


# Back-compat alias: M2.1 imported AudioSupervisor.
AudioSupervisor = InsightSupervisor
