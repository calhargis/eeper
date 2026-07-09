"""Reusable clip promotion — the M1.4 core, shared by the clips router (an admin
request) and the M2.4 nudge worker (an automatic pre/post-roll cut on a nudge).

A clip is an H.264 MP4 cut from the recorder's ring-buffer segments with ``-c copy``
(no re-encode, keyframe-aligned ±~1 GOP), stored under ``/media/clips`` where the
recorder's retention never scans it — so promoting a clip protects that footage from
eviction. This module builds the file and adds the ``Clip`` row to the caller's
session **flushed but not committed**, so the worker can set ``event.clip_id`` and
commit atomically (a crash before commit leaves an orphan file, never a half-linked
event)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import Clip
from eeper.recorder.layout import SegmentFile, clips_dir, scan_segments


class ClipPromotionError(Exception):
    """Clip promotion failed. ``code`` maps to an HTTP status in the router and is
    logged by the worker (which marks the event's clip_status "failed")."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        # code: window_too_long | no_coverage | segment_evicted | build_failed | no_video
        self.code = code


def _covering_segments(
    media_root: str, camera_id: int, start: datetime, end: datetime
) -> list[SegmentFile]:
    """Finalized segments whose time range overlaps [start, end]. A segment i is
    finalized iff a successor exists; its range is [segs[i].start, segs[i+1].start)."""
    segs = scan_segments(media_root, camera_id)
    covering: list[SegmentFile] = []
    for i in range(len(segs) - 1):  # exclude the newest (active) segment
        seg_start, seg_end = segs[i].start, segs[i + 1].start
        if seg_start < end and seg_end > start:
            covering.append(segs[i])
    return covering


async def _ffprobe(path: Path) -> dict[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=codec_name",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout or b"{}")
    streams = data.get("streams", [])
    codec = streams[0].get("codec_name", "") if streams else ""
    return {"codec": codec, "duration": data.get("format", {}).get("duration", "0")}


async def _run_clip_ffmpeg(
    list_path: Path, out_path: Path, offset_start: float, offset_end: float
) -> bool:
    # +genpts / -avoid_negative_ts make_zero keep the concatenated -c copy timeline
    # DTS-monotonic across segment boundaries; +faststart puts moov first for
    # progressive <video>. Output-side -ss/-to trim within the concat timeline.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-ss",
        f"{max(offset_start, 0.0):.3f}",
        "-to",
        f"{offset_end:.3f}",
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return proc.returncode == 0


async def promote_clip_for_window(
    *,
    session: AsyncSession,
    media_root: str,
    clip_max_seconds: int,
    household_id: str,
    camera_id: int,
    start: datetime,
    end: datetime,
) -> Clip:
    """Build a clip for [start, end] and add the ``Clip`` row to ``session`` (flushed,
    NOT committed — the caller commits). Raises :class:`ClipPromotionError` on any
    failure, leaving no partial row."""
    if (end - start).total_seconds() > clip_max_seconds:
        raise ClipPromotionError("window_too_long", f"Clip window exceeds {clip_max_seconds}s.")

    segments = _covering_segments(media_root, camera_id, start, end)
    if not segments:
        raise ClipPromotionError("no_coverage", "No recorded segments cover the window.")

    out_dir = clips_dir(media_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    out_path = out_dir / f"{token}.mp4"
    first_start = segments[0].start

    # Hard-link covering segments first so retention evicting the originals mid-build
    # can't silently truncate the concat (the inode stays alive while our link exists).
    build_dir = out_dir / f".build-{token}"
    build_dir.mkdir(parents=True, exist_ok=True)
    try:
        links: list[Path] = []
        try:
            for seg in segments:
                link = build_dir / seg.path.name
                os.link(seg.path, link)
                links.append(link)
        except FileNotFoundError as exc:
            raise ClipPromotionError(
                "segment_evicted", "A covering segment was evicted; retry a more recent window."
            ) from exc
        list_path = build_dir / "concat.txt"
        list_path.write_text("".join(f"file '{link}'\n" for link in links))
        ok = await _run_clip_ffmpeg(
            list_path,
            out_path,
            offset_start=(start - first_start).total_seconds(),
            offset_end=(end - first_start).total_seconds(),
        )
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    if not ok or not out_path.exists():
        raise ClipPromotionError("build_failed", "Could not build the clip.")

    probed = await _ffprobe(out_path)
    duration = float(probed["duration"] or 0.0)
    if not probed["codec"] or duration <= 0:
        with contextlib.suppress(FileNotFoundError):
            out_path.unlink()
        raise ClipPromotionError("no_video", "The window contains no recoverable video.")

    clip = Clip(
        household_id=household_id,
        camera_id=camera_id,
        path=str(out_path),
        requested_start=start,
        requested_end=end,
        actual_start=start,
        actual_end=start + timedelta(seconds=duration),
        duration_seconds=duration,
        size_bytes=out_path.stat().st_size,
        codec=probed["codec"],
    )
    session.add(clip)
    await session.flush()  # assign clip.id without committing — the caller commits
    return clip
