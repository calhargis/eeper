"""Standalone host-microphone listen-in (the audio adapter's `mic` stream).

Separate from a camera: when a host mic is configured (``EEPER_AUDIO_SOURCE_URL``), the
api registers its Opus stream in go2rtc as ``mic`` and relays WebRTC to it here, so a
household member can "listen to the room" with no camera selected. Viewing is open to
any authenticated member (grandparent mode); clients reach WebRTC only via this relay
— go2rtc is never exposed directly, exactly like the camera path.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from eeper.api.dependencies import CurrentUser, SettingsDep
from eeper.api.gateway import GatewayError, Go2rtcClient
from eeper.api.schemas import AudioStatusOut

router = APIRouter(prefix="/audio", tags=["audio"])


@router.get("", response_model=AudioStatusOut)
async def audio_status(user: CurrentUser, settings: SettingsDep) -> AudioStatusOut:
    """Whether a host microphone is available to listen to standalone."""
    return AudioStatusOut(available=bool(settings.audio_source_url))


@router.post("/webrtc")
async def audio_webrtc(request: Request, user: CurrentUser, settings: SettingsDep) -> Response:
    # Guard on config first so the no-mic case never touches the gateway (the raw
    # source_url is admin-only; the client only ever reaches the api-relayed stream).
    if not settings.audio_source_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No microphone is configured")
    offer = (await request.body()).decode("utf-8", errors="replace")
    if not offer.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Missing SDP offer")
    gateway: Go2rtcClient = request.app.state.gateway
    try:
        answer = await gateway.webrtc_answer(settings.mic_stream_name, offer)
    except GatewayError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Microphone stream is not available"
        ) from exc
    return Response(content=answer, media_type="application/sdp")
