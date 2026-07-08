"""Clip promotion + playback (M1.4).

A clip is an H.264 MP4 cut from the ring-buffer segments and stored under
``/media/clips`` (a subtree the recorder's retention never scans), so promoting a
clip protects that footage from eviction. Promotion is admin-only; viewing is any
authenticated household member. Clips are built with ``-c copy`` (no re-encode),
so the cut is keyframe-aligned (±~1 GOP) — the row stores both the requested and
the probed-actual window.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep, SettingsDep
from eeper.api.models import Camera, Clip, User
from eeper.api.schemas import ClipCreate, ClipOut, MessageOut
from eeper.recorder.layout import SegmentFile, clips_dir, scan_segments

router = APIRouter(tags=["clips"])


def _clip_out(clip: Clip) -> ClipOut:
    return ClipOut(
        id=clip.id,
        camera_id=clip.camera_id,
        requested_start=clip.requested_start,
        requested_end=clip.requested_end,
        actual_start=clip.actual_start,
        actual_end=clip.actual_end,
        duration_seconds=clip.duration_seconds,
        size_bytes=clip.size_bytes,
        codec=clip.codec,
        created_at=clip.created_at,
    )


async def _owned_camera(camera_id: int, user: User, session: AsyncSession) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.household_id != user.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return camera


async def _owned_clip(clip_id: int, user: User, session: AsyncSession) -> Clip:
    clip = await session.get(Clip, clip_id)
    if clip is None or clip.household_id != user.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip not found")
    return clip


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
    codec = ""
    streams = data.get("streams", [])
    if streams:
        codec = streams[0].get("codec_name", "")
    duration = data.get("format", {}).get("duration", "0")
    return {"codec": codec, "duration": duration}


@router.post(
    "/cameras/{camera_id}/clips", status_code=status.HTTP_201_CREATED, response_model=ClipOut
)
async def promote_clip(
    camera_id: int,
    body: ClipCreate,
    admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> ClipOut:
    camera = await _owned_camera(camera_id, admin, session)
    start, end = body.start, body.end
    if (end - start).total_seconds() > settings.clip_max_seconds:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Clip window exceeds the {settings.clip_max_seconds}s limit.",
        )

    segments = _covering_segments(settings.media_root, camera_id, start, end)
    if not segments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No recorded segments cover the requested window.",
        )

    out_dir = clips_dir(settings.media_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    out_path = out_dir / f"{token}.mp4"
    first_start = segments[0].start

    # Hard-link the covering segments into a private build dir first, so if the
    # recorder's retention evicts the originals mid-build the concat can't be
    # silently truncated — the inode stays alive while our link exists. Same
    # volume as /media/rec, and /media/clips is never scanned by retention.
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
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A covering segment was evicted during promotion; retry a more recent window.",
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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not build the clip.")

    probed = await _ffprobe(out_path)
    duration = float(probed["duration"] or 0.0)
    # A window past the last keyframe (or a build that lost frames) can leave a
    # zero-frame MP4; don't persist an empty, unplayable "clip".
    if not probed["codec"] or duration <= 0:
        with contextlib.suppress(FileNotFoundError):
            out_path.unlink()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The requested window contains no recoverable video.",
        )
    actual_start = start
    clip = Clip(
        household_id=camera.household_id,
        camera_id=camera_id,
        path=str(out_path),
        requested_start=start,
        requested_end=end,
        actual_start=actual_start,
        actual_end=actual_start + timedelta(seconds=duration),
        duration_seconds=duration,
        size_bytes=out_path.stat().st_size,
        codec=probed["codec"],
    )
    session.add(clip)
    await session.commit()
    await session.refresh(clip)
    return _clip_out(clip)


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


@router.get("/clips", response_model=list[ClipOut])
async def list_clips(user: CurrentUser, session: SessionDep) -> list[ClipOut]:
    result = await session.execute(
        select(Clip).where(Clip.household_id == user.household_id).order_by(Clip.id.desc())
    )
    return [_clip_out(c) for c in result.scalars().all()]


@router.get("/clips/{clip_id}", response_model=ClipOut)
async def get_clip(clip_id: int, user: CurrentUser, session: SessionDep) -> ClipOut:
    return _clip_out(await _owned_clip(clip_id, user, session))


@router.get("/clips/{clip_id}/media")
async def clip_media(clip_id: int, user: CurrentUser, session: SessionDep) -> FileResponse:
    clip = await _owned_clip(clip_id, user, session)
    if not Path(clip.path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip media not found")
    # Starlette's FileResponse serves HTTP Range (206/Accept-Ranges) natively, so a
    # browser <video> can seek. Same-origin under /api, so the CSP allows it.
    return FileResponse(clip.path, media_type="video/mp4")


@router.delete("/clips/{clip_id}", response_model=MessageOut)
async def delete_clip(clip_id: int, admin: AdminUser, session: SessionDep) -> MessageOut:
    clip = await _owned_clip(clip_id, admin, session)
    with contextlib.suppress(FileNotFoundError):
        Path(clip.path).unlink()
    await session.delete(clip)
    await session.commit()
    return MessageOut(detail="Clip removed")
