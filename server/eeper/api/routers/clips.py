"""Clip promotion + playback (M1.4).

A clip is an H.264 MP4 cut from the ring-buffer segments and stored under
``/media/clips`` (a subtree the recorder's retention never scans), so promoting a
clip protects that footage from eviction. Promotion is admin-only; viewing is any
authenticated household member. The build/cut logic lives in
:mod:`eeper.api.clip_service` (shared with the M2.4 nudge worker's auto-promotion).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.clip_service import ClipPromotionError, promote_clip_for_window
from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep, SettingsDep
from eeper.api.models import Camera, Clip, User
from eeper.api.schemas import ClipCreate, ClipOut, MessageOut

router = APIRouter(tags=["clips"])

_PROMOTE_STATUS = {
    "window_too_long": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "no_coverage": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "segment_evicted": status.HTTP_409_CONFLICT,
    "build_failed": status.HTTP_502_BAD_GATEWAY,
    "no_video": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


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
    try:
        clip = await promote_clip_for_window(
            session=session,
            media_root=settings.media_root,
            clip_max_seconds=settings.clip_max_seconds,
            household_id=camera.household_id,
            camera_id=camera_id,
            start=body.start,
            end=body.end,
        )
    except ClipPromotionError as exc:
        raise HTTPException(_PROMOTE_STATUS[exc.code], str(exc)) from exc
    await session.commit()
    await session.refresh(clip)
    return _clip_out(clip)


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
