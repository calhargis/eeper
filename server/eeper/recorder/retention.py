"""Ring-buffer retention: evict FINALIZED recording segments by AGE (older than a
configured max age, if set) and by QUOTA (oldest-first once the tree exceeds the byte
budget).

Safety against deleting a segment that is still being written comes solely from
the newest-per-camera exclusion (``scan_segments()[:-1]`` / the ``i < len-1``
guard below): the on-disk writer is an independent ffmpeg subprocess, NOT the
recorder Python process, so there is no lock or same-process serialization — the
load-bearing invariant is that ffmpeg only ever writes the highest-strftime file,
so never deleting that file is what keeps eviction off the active segment. Both
policies operate only on that finalized set, so neither can touch the active segment.
``/media/clips`` is never scanned/evicted, so promoted clips survive by construction.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from eeper.api.config import Settings
from eeper.recorder.layout import SegmentFile, rec_root, scan_segments

_log = logging.getLogger("eeper.recorder.retention")


def _evict(seg: SegmentFile) -> int:
    """Delete a segment; return the bytes freed (its size if removed or already gone,
    0 if it couldn't be removed)."""
    try:
        seg.path.unlink()
        _log.info("evicted %s (%d bytes)", seg.path.name, seg.size)
        return seg.size
    except FileNotFoundError:
        return seg.size  # already gone; still count it as freed
    except OSError:
        _log.warning("could not evict %s", seg.path)
        return 0


def evict_once(settings: Settings, now: datetime | None = None) -> None:
    root = rec_root(settings.media_root)
    if not root.exists():
        return
    now = now or datetime.now(UTC)
    total = 0
    finalized: list[SegmentFile] = []
    for cam_dir in root.iterdir():
        if not cam_dir.is_dir() or not cam_dir.name.startswith("cam"):
            continue
        try:
            camera_id = int(cam_dir.name[len("cam") :])
        except ValueError:
            continue
        segs = scan_segments(settings.media_root, camera_id)
        for i, seg in enumerate(segs):
            total += seg.size
            if i < len(segs) - 1:  # exclude the newest (active/truncatable) segment
                finalized.append(seg)

    finalized.sort(key=lambda s: s.start)  # oldest first, across all cameras

    # Age policy: evict anything older than the cutoff, regardless of quota.
    if settings.media_max_age_seconds > 0:
        cutoff = now - timedelta(seconds=settings.media_max_age_seconds)
        kept: list[SegmentFile] = []
        for seg in finalized:
            if seg.start < cutoff:
                total -= _evict(seg)
            else:
                kept.append(seg)
        finalized = kept

    # Quota policy: evict oldest-first until back under the byte budget.
    for seg in finalized:
        if total <= settings.media_quota_bytes:
            break
        total -= _evict(seg)


async def retention_loop(settings: Settings) -> None:
    while True:
        try:
            evict_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # a tick must never kill the loop
            _log.exception("retention tick failed")
        await asyncio.sleep(settings.retention_interval_seconds)
