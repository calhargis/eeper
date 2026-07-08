"""Fixed-size gray-frame sampling for the camera motion extractor.

The video stage decodes each camera to raw 8-bit gray frames, downscaled to
``width x height`` at ``fps`` (M2.2). Frames land in a small per-camera ring that
the motion scorer consumes in-process. Unlike the audio ``WindowRing``, this ring
is LATEST-WINS: when full it drops the oldest frame on append, so the pipe drain
never blocks and memory stays bounded no matter how far behind the scorer falls —
this drop IS the backpressure mechanism.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FrameSpec:
    width: int = 160
    height: int = 120
    fps: int = 5

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height  # 1 byte/px gray -> 19200


FRAME_SPEC = FrameSpec()


class FrameRing:
    """Accumulates a raw gray byte stream and slices complete frames on exact
    ``frame_bytes`` boundaries into a bounded, latest-wins deque. Boundary-based
    slicing keeps frames exact regardless of how the stream is chunked off the pipe.
    """

    def __init__(self, spec: FrameSpec = FRAME_SPEC, maxlen: int = 3) -> None:
        # maxlen must be >= 3 so ``snapshot`` can return a stable consecutive
        # (prev, cur) pair even if a concurrent feed evicts one frame between the
        # two indexed reads.
        self._spec = spec
        self._buf = bytearray()
        self.frames: collections.deque[bytes] = collections.deque(maxlen=maxlen)
        self.frames_fed = 0  # total complete frames ever fed (monotonic)
        self.last_feed_monotonic = 0.0

    def feed(self, data: bytes) -> int:
        """Append raw bytes; emit any complete frames into the ring. Returns the
        number of frames emitted this call. The oldest frame is silently evicted
        when the ring is full — the intentional backpressure drop."""
        self._buf.extend(data)
        emitted = 0
        frame_bytes = self._spec.frame_bytes
        while len(self._buf) >= frame_bytes:
            frame = bytes(self._buf[:frame_bytes])
            del self._buf[:frame_bytes]
            self.frames.append(frame)
            self.frames_fed += 1
            emitted += 1
        if emitted:
            self.last_feed_monotonic = time.monotonic()
        return emitted

    def snapshot(self) -> tuple[bytes | None, bytes | None, int]:
        """Return ``(prev, cur, cur_index)`` captured in ONE synchronous read (no
        await between the indexed accesses), so the pair is a stable consecutive
        pair even under a concurrent feed. ``cur_index`` is the monotonic index of
        ``cur`` (``frames_fed - 1``): because the ring only ever holds the newest
        frames, this always reflects the freshest available pair, so a slow scorer
        jumps past any dropped backlog rather than draining stale frames.
        Returns ``(None, None, cur_index)`` until at least two frames exist."""
        frames = self.frames
        cur_index = self.frames_fed - 1
        if len(frames) < 2:
            return None, None, cur_index
        return frames[-2], frames[-1], cur_index
