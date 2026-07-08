"""Test tap: when EEPER_INSIGHT_TAP_DIR is set, write the newest motion state per
camera as cam{id}.motion.json (temp file + atomic rename), so the integration test
can observe the score / level / freshness sub-second without an API. Unset in
production — zero cost, no surface. Writes to a tmpfs, off the scoring hot path.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class MotionTap:
    def __init__(self, tap_dir: str) -> None:
        self._dir = Path(tap_dir) if tap_dir else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def write(self, camera_id: int, payload: dict[str, Any]) -> None:
        if self._dir is None:
            return
        target = self._dir / f"cam{camera_id}.motion.json"
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle)
            os.replace(tmp, target)  # atomic; readers never see a partial file
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)  # only lingers if the write failed before replace
