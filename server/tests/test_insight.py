"""Unit tests for the insight supervisor: PCM windowing + per-stream reaping."""

from __future__ import annotations

import asyncio
import contextlib

from eeper.api.config import Settings
from eeper.insight.frame import FRAME_SPEC, FrameRing
from eeper.insight.supervisor import InsightSupervisor, _Stream
from eeper.insight.window import SPEC, WindowRing


def test_window_ring_frames_on_exact_boundaries() -> None:
    ring = WindowRing(SPEC)
    # A partial buffer emits nothing.
    assert ring.feed(b"\x00" * (SPEC.window_bytes - 1)) == []
    # Completing the boundary emits exactly one 32000-byte window.
    emitted = ring.feed(b"\x00")
    assert len(emitted) == 1
    assert len(emitted[0]) == SPEC.window_bytes == 32000
    # 2.5 windows -> 2 emitted, the half remainder stays buffered.
    more = ring.feed(b"\x01" * (SPEC.window_bytes * 2 + SPEC.window_bytes // 2))
    assert len(more) == 2
    assert all(len(w) == SPEC.window_bytes for w in more)
    # The ring is bounded.
    assert ring.windows.maxlen is not None
    assert len(ring.windows) <= ring.windows.maxlen


class _StubSupervisor(InsightSupervisor):
    """Overrides camera discovery + spawn so reconcile runs without a DB or real
    ffmpeg; the real _stop_stream still runs (it tears a child down)."""

    def __init__(self, settings: Settings, desired: dict[int, bool]) -> None:
        super().__init__(sessionmaker=None, settings=settings)  # type: ignore[arg-type]
        self._desired = desired
        self.spawned_video: list[int] = []
        self.spawned_audio: list[int] = []

    async def _desired_cameras(self) -> dict[int, bool]:
        return self._desired

    async def _spawn_video(self, camera_id: int) -> None:
        self.spawned_video.append(camera_id)

    async def _spawn_audio(self, camera_id: int) -> None:
        self.spawned_audio.append(camera_id)


async def _alive_child() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec("sleep", "30", stdout=asyncio.subprocess.DEVNULL)


async def test_reconcile_reaps_video_stream_without_touching_audio() -> None:
    # Per-stream reap regression: a video child that is still alive (returncode
    # None) but whose reader task has ended must be reaped WITH its scorer, while a
    # healthy audio stream on the same camera is left running (listen-in must not
    # drop when motion hiccups).
    settings = Settings(database_url="postgresql+asyncpg://x/y", secret_key="0" * 16)
    sup = _StubSupervisor(settings, desired={1: True})

    # video: alive child + a FINISHED reader (the wedge scenario) + a live scorer.
    vproc = await _alive_child()
    vreader: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(0))
    await vreader
    sup._video[1] = _Stream(vproc, vreader, FrameRing(FRAME_SPEC))
    scorer: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(3600))
    sup._scorers[1] = scorer

    # audio: alive child + an ALIVE reader (healthy).
    aproc = await _alive_child()
    areader: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(3600))
    sup._audio[1] = _Stream(aproc, areader, WindowRing(SPEC))

    try:
        await sup.reconcile()
        # video reaped: stream gone, child terminated, scorer stopped, on backoff,
        # not respawned this tick.
        assert 1 not in sup._video, "wedged video stream was not reaped"
        assert vproc.returncode is not None, "reaped video child was not terminated"
        assert scorer.done(), "scorer was not stopped with its video stream"
        assert (1, "video") in sup._backoff, "reaped video stream not put on backoff"
        assert sup.spawned_video == [], "video should not respawn while on backoff"
        # audio untouched: present, child alive, reader alive.
        assert 1 in sup._audio, "healthy audio stream was torn down with the video reap"
        assert aproc.returncode is None, "healthy audio child was killed"
        assert not areader.done(), "healthy audio reader was cancelled"
    finally:
        for proc in (vproc, aproc):
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        for task in (scorer, areader):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_video_only_camera_spawns_no_audio_child() -> None:
    # C5 (stub half): a video-only camera (has_audio=False) gets a video stream +
    # scorer, no audio child, and its active extractors are exactly {"motion"}.
    settings = Settings(database_url="postgresql+asyncpg://x/y", secret_key="0" * 16)
    sup = _StubSupervisor(settings, desired={7: False})
    await sup.reconcile()
    assert sup.spawned_video == [7]
    assert sup.spawned_audio == [], "video-only camera must not spawn an audio child"
    assert sup.active_extractors[7] == frozenset({"motion"})
