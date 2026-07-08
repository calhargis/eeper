"""Test tap: when EEPER_INSIGHT_TAP_DIR is set, write the newest audio window per
camera as a self-describing mono/16k/s16 WAV (temp file + atomic rename). Unset in
production, so the tap has zero cost and no surface there. Writes to a tmpfs, off
the pipe-drain path.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import wave
from pathlib import Path

from eeper.insight.window import WindowSpec


class WavTap:
    def __init__(self, tap_dir: str, spec: WindowSpec) -> None:
        self._dir = Path(tap_dir) if tap_dir else None
        self._spec = spec
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def write(self, camera_id: int, window: bytes) -> None:
        if self._dir is None:
            return
        target = self._dir / f"cam{camera_id}.wav"
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        os.close(fd)
        try:
            with wave.open(tmp, "wb") as wav:
                wav.setnchannels(self._spec.channels)
                wav.setsampwidth(self._spec.bytes_per_sample)
                wav.setframerate(self._spec.sample_rate)
                wav.writeframes(window)
            os.replace(tmp, target)  # atomic; readers never see a partial WAV
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)  # only lingers if the write failed before replace
