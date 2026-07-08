"""C4 (deterministic half): the frame ring is bounded and drops the backlog."""

from __future__ import annotations

from eeper.insight.frame import FrameRing, FrameSpec


def test_frames_sliced_on_exact_boundaries() -> None:
    spec = FrameSpec(width=4, height=2, fps=5)  # frame_bytes = 8
    ring = FrameRing(spec, maxlen=3)
    assert ring.feed(b"\x00" * 7) == 0  # partial: nothing emitted
    assert ring.feed(b"\x00") == 1  # completes exactly one frame
    assert ring.feed(b"\x01" * 20) == 2  # 2 whole frames + a 4-byte remainder
    assert all(len(f) == spec.frame_bytes for f in ring.frames)


def test_ring_is_bounded_and_drops_oldest_under_backlog() -> None:
    # Feed far more frames than maxlen with no consumer; memory never grows past
    # maxlen frames + a sub-frame remainder, independent of the producer/consumer
    # speed skew. This is the deterministic memory-bound proof for C4.
    spec = FrameSpec(width=10, height=10, fps=5)  # 100 bytes/frame
    ring = FrameRing(spec, maxlen=3)
    for _ in range(1000):
        ring.feed(bytes(spec.frame_bytes))
    assert len(ring.frames) <= 3
    assert ring.frames.maxlen == 3
    assert ring.frames_fed == 1000


def test_snapshot_returns_freshest_consecutive_pair_past_a_backlog() -> None:
    # A slow scorer that snapshots only after a large backlog gets the two NEWEST
    # frames — proving it jumps past dropped frames rather than draining a stale head.
    spec = FrameSpec(width=2, height=2, fps=5)  # 4 bytes/frame
    ring = FrameRing(spec, maxlen=3)
    for i in range(50):
        ring.feed(bytes([i % 256]) * spec.frame_bytes)
    prev, cur, idx = ring.snapshot()
    assert prev is not None and cur is not None
    assert idx == ring.frames_fed - 1 == 49
    assert idx >= ring.frames_fed - 2  # scorer stayed within the newest two frames
    assert cur == bytes([49]) * spec.frame_bytes
    assert prev == bytes([48]) * spec.frame_bytes


def test_snapshot_needs_two_frames() -> None:
    spec = FrameSpec(width=2, height=2, fps=5)
    ring = FrameRing(spec, maxlen=3)
    assert ring.snapshot() == (None, None, -1)
    ring.feed(bytes(spec.frame_bytes))
    prev, cur, idx = ring.snapshot()
    assert prev is None and cur is None and idx == 0
