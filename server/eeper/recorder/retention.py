"""Ring-buffer retention: evict the oldest FINALIZED segments once the recording
tree exceeds the byte quota.

Safety against deleting a segment that is still being written comes solely from
the newest-per-camera exclusion (``scan_segments()[:-1]`` / the ``i < len-1``
guard below): the on-disk writer is an independent ffmpeg subprocess, NOT the
recorder Python process, so there is no lock or same-process serialization — the
load-bearing invariant is that ffmpeg only ever writes the highest-strftime file,
so never deleting that file is what keeps eviction off the active segment.
``/media/clips`` is never scanned/evicted, so promoted clips survive by construction.
"""

from __future__ import annotations

import asyncio
import logging

from eeper.api.config import Settings
from eeper.recorder.layout import SegmentFile, rec_root, scan_segments

_log = logging.getLogger("eeper.recorder.retention")


def evict_once(settings: Settings) -> None:
    root = rec_root(settings.media_root)
    if not root.exists():
        return
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

    if total <= settings.media_quota_bytes:
        return

    finalized.sort(key=lambda s: s.start)  # oldest first, across all cameras
    for seg in finalized:
        if total <= settings.media_quota_bytes:
            break
        try:
            seg.path.unlink()
            _log.info("evicted %s (%d bytes)", seg.path.name, seg.size)
        except FileNotFoundError:
            pass  # already gone; still count it as freed below
        except OSError:
            _log.warning("could not evict %s", seg.path)
            continue
        total -= seg.size


async def retention_loop(settings: Settings) -> None:
    while True:
        try:
            evict_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # a tick must never kill the loop
            _log.exception("retention tick failed")
        await asyncio.sleep(settings.retention_interval_seconds)
