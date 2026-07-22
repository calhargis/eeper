"""Integration tests for the host-microphone audio adapter.

Runs against core + video (synthetic camera) + the audio adapter, all brought up
together with EEPER_AUDIO_SOURCE_URL pointed at the adapter. The adapter uses a
synthetic sine input (no ALSA_DEVICE) through the SAME encode + RTSP serve path a
real USB mic takes — hosted runners have no ALSA capture device, so the required
gate exercises everything but the literal device open, which is the [MANUAL] Pi
bench item (see docs/ci.md).

Proves the plumbing the merge relies on: the adapter serves an Opus stream, the api
reports the mic available, relays a standalone `mic` WebRTC session, and merges the
mic's audio track into the camera's stream. The sound-level NUDGE off a real mic is
verified on the Pi bench (a constant sine is absorbed by the adaptive baseline, so
it can't fire the nudge in CI by design).
"""

from __future__ import annotations

import asyncio
import os
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription

DEPLOY_DIR = Path(__file__).resolve().parents[2]
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"
BASE_URL = os.environ.get("EEPER_TEST_URL", "https://localhost")
ADMIN, PASSWORD = "admin", "correct horse battery staple"
CAMERA_SOURCE = "rtsp://synthetic-camera:8554/cam"
MIC_SOURCE = "rtsp://audio-adapter:8554/mic"

_COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(DEPLOY_DIR / "docker-compose.yml"),
    "-f",
    str(DEPLOY_DIR / "video-test.yml"),
    "-f",
    str(DEPLOY_DIR / "audio-adapter-test.yml"),
    "--profile",
    "core",
    "--profile",
    "video",
]


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_COMPOSE, *args], cwd=DEPLOY_DIR, capture_output=True, text=True, check=False
    )


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(CA_PATH))


@pytest.fixture(scope="session")
def admin() -> httpx.Client:
    assert CA_PATH.exists(), f"local CA not found at {CA_PATH}"
    with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=20) as client:
        for _ in range(60):
            try:
                if client.get("/api/v1/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(2)
        created = client.post(
            "/api/v1/system/first-boot", json={"username": ADMIN, "password": PASSWORD}
        )
        if created.status_code == 409:
            login = client.post(
                "/api/v1/auth/login", json={"username": ADMIN, "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
        else:
            assert created.status_code == 201, created.text
        yield client


@pytest.fixture(scope="session")
def camera_id(admin: httpx.Client) -> int:
    # The synthetic camera may still be warming up right after `up`; retry so a
    # transient 502 (stream not yet published) doesn't fail the suite.
    created = None
    for _ in range(6):
        created = admin.post(
            "/api/v1/cameras", json={"name": "nursery", "source_url": CAMERA_SOURCE}
        )
        if created.status_code in (201, 409):
            break
        time.sleep(2)
    assert created is not None
    if created.status_code == 409:
        listing = admin.get("/api/v1/cameras").json()
        return int(next(c["id"] for c in listing if c["name"] == "nursery"))
    assert created.status_code == 201, created.text
    body = created.json()
    # The host mic is merged into every camera stream, so the camera is audio-capable
    # (its listen-in control appears) regardless of its own source's tracks.
    assert body["has_audio"] is True
    return int(body["id"])


def test_mic_is_available(admin: httpx.Client) -> None:
    assert admin.get("/api/v1/audio").json() == {"available": True}


def test_mic_stream_is_opus(admin: httpx.Client, camera_id: int) -> None:
    # The adapter's standalone `mic` stream re-serves over go2rtc's internal RTSP as
    # an Opus audio track (the WebRTC-native audio codec) — proving the ALSA→Opus→RTSP
    # adapter path end to end.
    probe = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        result = _compose(
            "exec",
            "-T",
            "api",
            "ffprobe",
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            "rtsp://go2rtc:8554/mic",
        )
        probe = result.stdout.strip()
        if "opus" in probe:
            return
        time.sleep(1)
    raise AssertionError(f"mic stream is not Opus over RTSP: {probe!r}")


def test_standalone_mic_webrtc_answers_audio(admin: httpx.Client) -> None:
    async def negotiate() -> None:
        pc = RTCPeerConnection()
        pc.addTransceiver("audio", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        response = admin.post(
            "/api/v1/audio/webrtc",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        assert response.status_code == 200, response.text
        assert "m=audio" in response.text
        await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))
        await pc.close()

    asyncio.run(negotiate())


def test_camera_stream_carries_the_merged_mic(admin: httpx.Client, camera_id: int) -> None:
    # A camera WebRTC offer for video + audio comes back with BOTH tracks: the mic's
    # Opus audio is merged into the camera's stream, so watch + listen ride together.
    async def negotiate() -> None:
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        response = admin.post(
            f"/api/v1/cameras/{camera_id}/webrtc",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        assert response.status_code == 200, response.text
        assert "m=video" in response.text and "m=audio" in response.text
        await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))
        await pc.close()

    asyncio.run(negotiate())


def test_audio_adapter_container_hardened() -> None:
    cid = _compose("ps", "-q", "audio-adapter").stdout.strip()
    assert cid, "audio-adapter not running"
    out = subprocess.run(
        ["docker", "inspect", cid, "--format",
         "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    user, readonly, cap_drop = out.split("|")
    assert user and user not in ("root", "0", "0:0"), f"adapter runs as root ({user!r})"
    assert readonly == "true", "adapter rootfs is not read-only"
    assert "ALL" in cap_drop, f"adapter does not drop all capabilities ({cap_drop})"
