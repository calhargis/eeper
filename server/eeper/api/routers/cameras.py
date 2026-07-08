"""Camera registration + the WebRTC signaling relay.

Registration validates the source against the RTSP contract (H.264, <=1080p) with
ffprobe, stores it, and registers the stream in go2rtc. Management is admin-only;
viewing (list/get/webrtc) is open to any authenticated household member so a
viewer ('grandparent mode') can watch. Clients reach WebRTC only via this relay —
go2rtc is never exposed directly.
"""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.camera_monitor import CameraMonitor, stream_name
from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep, SettingsDep
from eeper.api.gateway import GatewayError, Go2rtcClient
from eeper.api.models import Camera, User
from eeper.api.probe import ProbeRejected, ProbeUnavailable, probe_has_audio, probe_video
from eeper.api.schemas import CameraCreate, CameraOut, MessageOut

router = APIRouter(prefix="/cameras", tags=["cameras"])


def get_gateway(request: Request) -> Go2rtcClient:
    return request.app.state.gateway  # type: ignore[no-any-return]


def get_monitor(request: Request) -> CameraMonitor:
    return request.app.state.monitor  # type: ignore[no-any-return]


GatewayDep = Annotated[Go2rtcClient, Depends(get_gateway)]
MonitorDep = Annotated[CameraMonitor, Depends(get_monitor)]


def _camera_out(camera: Camera, monitor: CameraMonitor) -> CameraOut:
    health = monitor.get_health(camera.id)
    return CameraOut(
        id=camera.id,
        name=camera.name,
        codec=camera.codec,
        width=camera.width,
        height=camera.height,
        enabled=camera.enabled,
        has_audio=camera.has_audio,
        online=health.online if health else None,
        last_checked=health.last_checked if health else None,
    )


async def _owned_camera(camera_id: int, user: User, session: AsyncSession) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.household_id != user.household_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return camera


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CameraOut)
async def register_camera(
    body: CameraCreate,
    admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
    monitor: MonitorDep,
) -> CameraOut:
    # Reject an obvious duplicate before spending a probe on it (the DB unique
    # constraint is the race-proof backstop, below).
    duplicate = await session.execute(
        select(Camera.id).where(
            Camera.household_id == admin.household_id,
            Camera.source_url == body.source_url,
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This camera source is already registered.")

    # Validate the source against the contract before storing anything. A
    # reachable-but-non-conformant source (no video stream) is a 422 contract
    # rejection; a genuinely unreachable source is a 502.
    try:
        info = await probe_video(body.source_url, settings.probe_timeout_seconds)
    except ProbeRejected as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The camera source has no usable video stream ({exc}).",
        ) from exc
    except ProbeUnavailable as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not read the camera stream: {exc}"
        ) from exc
    if info.codec != "h264":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported video codec '{info.codec}'. eeper requires H.264 "
            "(H.265/HEVC is not supported).",
        )
    short_edge, long_edge = sorted((info.width, info.height))
    if short_edge > settings.max_video_short_edge or long_edge > settings.max_video_long_edge:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Resolution {info.width}x{info.height} exceeds the 1080p limit.",
        )

    has_audio = await probe_has_audio(body.source_url, settings.probe_timeout_seconds)

    camera = Camera(
        household_id=admin.household_id,
        name=body.name,
        source_url=body.source_url,
        codec=info.codec,
        width=info.width,
        height=info.height,
        has_audio=has_audio,
    )
    session.add(camera)
    try:
        await session.commit()
    except IntegrityError as exc:  # lost a race with a concurrent duplicate register
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This camera source is already registered."
        ) from exc
    await session.refresh(camera)

    try:
        await monitor.register(camera)
    except GatewayError as exc:
        await session.delete(camera)  # don't leave a camera the gateway doesn't know
        await session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Media gateway is unavailable") from exc

    # Confirm the source is live and seed health.
    await monitor.probe(camera)
    return _camera_out(camera, monitor)


@router.get("", response_model=list[CameraOut])
async def list_cameras(
    user: CurrentUser, session: SessionDep, monitor: MonitorDep
) -> list[CameraOut]:
    result = await session.execute(
        select(Camera).where(Camera.household_id == user.household_id).order_by(Camera.id)
    )
    return [_camera_out(c, monitor) for c in result.scalars().all()]


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(
    camera_id: int, user: CurrentUser, session: SessionDep, monitor: MonitorDep
) -> CameraOut:
    camera = await _owned_camera(camera_id, user, session)
    return _camera_out(camera, monitor)


@router.delete("/{camera_id}", response_model=MessageOut)
async def delete_camera(
    camera_id: int,
    admin: AdminUser,
    session: SessionDep,
    gateway: GatewayDep,
    monitor: MonitorDep,
) -> MessageOut:
    camera = await _owned_camera(camera_id, admin, session)
    with contextlib.suppress(GatewayError):  # best-effort; still remove from the DB
        await gateway.remove_stream(stream_name(camera_id))
    monitor.forget(camera_id)
    await session.delete(camera)
    await session.commit()
    return MessageOut(detail="Camera removed")


@router.post("/{camera_id}/webrtc")
async def camera_webrtc(
    camera_id: int,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    gateway: GatewayDep,
) -> Response:
    camera = await _owned_camera(camera_id, user, session)
    offer = (await request.body()).decode("utf-8", errors="replace")
    if not offer.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Missing SDP offer")
    try:
        answer = await gateway.webrtc_answer(stream_name(camera.id), offer)
    except GatewayError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Stream is not available") from exc
    return Response(content=answer, media_type="application/sdp")
