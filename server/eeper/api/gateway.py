"""Thin async client for the go2rtc media gateway's REST API.

Streams are registered dynamically here; go2rtc re-serves them over internal RTSP
and answers WebRTC offers. The gateway is internal-only, so clients reach WebRTC
signaling only through the api relay that calls :meth:`webrtc_answer`.
"""

from __future__ import annotations

import httpx


class GatewayError(Exception):
    """The media gateway could not satisfy the request."""


class Go2rtcClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def add_stream(self, name: str, source: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.put(
                    f"{self._base}/api/streams", params={"name": name, "src": source}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"failed to register stream {name!r}") from exc

    async def remove_stream(self, name: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.delete(f"{self._base}/api/streams", params={"src": name})
                if response.status_code not in (200, 404):
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"failed to remove stream {name!r}") from exc

    async def stream_names(self) -> set[str]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base}/api/streams")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise GatewayError("failed to list streams") from exc
        return set(data.keys()) if isinstance(data, dict) else set()

    async def webrtc_answer(self, name: str, offer_sdp: str) -> str:
        """Relay a WebRTC SDP offer to go2rtc and return its SDP answer."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout + 5) as client:
                response = await client.post(
                    f"{self._base}/api/webrtc",
                    params={"src": name},
                    content=offer_sdp,
                    headers={"Content-Type": "application/sdp"},
                )
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as exc:
            raise GatewayError(f"stream {name!r} is not available") from exc
