"""Fixed-size PCM windowing.

The audio stage decodes each camera to 16 kHz mono s16le and slices it into
non-overlapping 1.0 s windows (16000 samples = 32000 bytes) on exact byte
boundaries. Windows land in a small per-camera ring that M2.2 feature extractors
will consume in-process; the test tap reads the newest window.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowSpec:
    sample_rate: int = 16000
    channels: int = 1
    bytes_per_sample: int = 2  # s16le
    samples_per_window: int = 16000  # 1.0 s

    @property
    def window_bytes(self) -> int:
        return self.samples_per_window * self.channels * self.bytes_per_sample  # 32000


SPEC = WindowSpec()


class WindowRing:
    """Accumulates a PCM byte stream and emits complete windows on exact
    ``window_bytes`` boundaries, keeping the most recent windows in a bounded
    deque. Slicing is boundary-based, never per-read, so windows are exact
    regardless of how the stream is chunked off the pipe."""

    def __init__(self, spec: WindowSpec = SPEC, maxlen: int = 8) -> None:
        self._spec = spec
        self._buf = bytearray()
        self.windows: collections.deque[bytes] = collections.deque(maxlen=maxlen)
        # Monotonic count of windows ever emitted (never wraps with the deque). The
        # audio scorer diffs this against its own cursor to score only new windows and
        # to detect (and drop) a backlog under load — the same freshness bookkeeping
        # the frame ring uses.
        self.windows_emitted = 0

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        emitted: list[bytes] = []
        window_bytes = self._spec.window_bytes
        while len(self._buf) >= window_bytes:
            window = bytes(self._buf[:window_bytes])
            del self._buf[:window_bytes]
            self.windows.append(window)
            self.windows_emitted += 1
            emitted.append(window)
        return emitted
