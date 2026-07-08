"""Unit tests for the insight audio pipeline: PCM windowing + supervisor reaping."""

from __future__ import annotations

import asyncio

from eeper.api.config import Settings
from eeper.insight.supervisor import AudioSupervisor
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


class _StubSupervisor(AudioSupervisor):
    """Overrides camera discovery + spawn so reconcile can be exercised without a
    DB or real ffmpeg; the real _stop_child still runs (it tears the child down)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(sessionmaker=None, settings=settings)  # type: ignore[arg-type]
        self.spawned: list[int] = []

    async def _enabled_camera_ids(self) -> set[int]:
        return {1}

    async def _spawn(self, camera_id: int) -> None:
        self.spawned.append(camera_id)


async def test_reconcile_reaps_child_whose_reader_finished() -> None:
    # The regression guard for the drain-wedge fix: a child that is still alive
    # (returncode None) but whose reader task has ended must be reaped, not left
    # running with an undrained pipe.
    settings = Settings(database_url="postgresql+asyncpg://x/y", secret_key="0" * 16)
    sup = _StubSupervisor(settings)

    # A real, alive child (returncode stays None) plus a reader task that finished.
    proc = await asyncio.create_subprocess_exec("sleep", "30", stdout=asyncio.subprocess.DEVNULL)
    done_reader: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(0))
    await done_reader
    sup._children[1] = proc
    sup._readers[1] = done_reader

    try:
        await sup.reconcile()
        assert 1 not in sup._children, "live child with a finished reader was not reaped"
        assert 1 in sup._backoff_until, "reaped child was not put on respawn backoff"
        assert sup.spawned == [], "should not respawn immediately (backoff active)"
        assert proc.returncode is not None, "the reaped child was not terminated"
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
