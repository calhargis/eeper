"""The one shared media-layout module — imported by BOTH the recorder and the api
so they can never drift on paths or naming.

The filesystem IS the segment index (no DB table): the recorder writes each
segment directly to its final strftime name, and ffmpeg only opens segment N+1
after closing N. So "a segment whose strictly-newer sibling exists is finalized"
is a crash-safe completeness signal that cannot desync from the files — the whole
basis of the M1.4 crash-safety guarantee. The newest ``.ts`` per camera is the
possibly-active (truncatable) segment and is excluded from clip end-boundaries and
from eviction until a newer sibling appears.

Restart caveat: after a SIGKILL the once-active segment may be truncated, and when
the respawned ffmpeg writes a newer segment that truncated file gains a newer
sibling and is treated as finalized. That's acceptable — MPEG-TS truncation leaves
a valid prefix, so a clip spanning it is at most ~one segment shorter (the same
tolerance class as keyframe alignment), never corrupt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SEG_SUFFIX = ".ts"
# UTC strftime; the same codes are used by ffmpeg's -strftime output naming and by
# parse_seg_start below, so the written name and the parsed start always agree.
SEG_NAME_FMT = "%Y%m%d-%H%M%S"
SEG_OUTPUT_PATTERN = SEG_NAME_FMT + SEG_SUFFIX  # handed to ffmpeg -strftime


def rec_root(media_root: str) -> Path:
    return Path(media_root) / "rec"


def seg_dir(media_root: str, camera_id: int) -> Path:
    return rec_root(media_root) / f"cam{camera_id}"


def clips_dir(media_root: str) -> Path:
    return Path(media_root) / "clips"


def parse_seg_start(name: str) -> datetime | None:
    """Parse a segment file's start time from its name; None if it doesn't match."""
    stem = name[: -len(SEG_SUFFIX)] if name.endswith(SEG_SUFFIX) else name
    try:
        return datetime.strptime(stem, SEG_NAME_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(frozen=True)
class SegmentFile:
    path: Path
    start: datetime
    size: int


def scan_segments(media_root: str, camera_id: int) -> list[SegmentFile]:
    """All segment files for a camera, ascending by start time. Robust to a
    segment being rolled/deleted mid-scan."""
    out: list[SegmentFile] = []
    try:
        entries = list(os.scandir(seg_dir(media_root, camera_id)))
    except FileNotFoundError:
        return []
    for entry in entries:
        if not entry.name.endswith(SEG_SUFFIX):
            continue
        start = parse_seg_start(entry.name)
        if start is None:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue  # rolled/deleted between scandir and stat
        out.append(SegmentFile(Path(entry.path), start, size))
    out.sort(key=lambda s: s.start)
    return out


def finalized_segments(media_root: str, camera_id: int) -> list[SegmentFile]:
    """Segments guaranteed complete — all but the newest (the active/truncatable
    one). Used for clip boundaries and eviction."""
    segs = scan_segments(media_root, camera_id)
    return segs[:-1]
