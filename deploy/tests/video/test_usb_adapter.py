"""Integration tests for the M1.3 USB (V4L2) camera adapter.

Runs against core + video (synthetic camera) + the USB adapter, all brought up
together. The adapter uses a synthetic lavfi input (no V4L2_DEVICE) through the
SAME encode + RTSP serve path a real webcam takes — hosted CI runners can't load
the v4l2loopback kernel module, so the required gate exercises everything but the
literal device open, which is the [MANUAL] bench item (see docs/ci.md). Reuses the
native-camera contract assertions: registration passes the ffprobe contract, the
internal re-serve is H.264, the WebRTC relay answers, and the adapter is hardened.
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
ADAPTER_SOURCE = "rtsp://usb-adapter:8554/cam"

_COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(DEPLOY_DIR / "docker-compose.yml"),
    "-f",
    str(DEPLOY_DIR / "video-test.yml"),
    "-f",
    str(DEPLOY_DIR / "adapter-test.yml"),
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
def adapter_camera_id(admin: httpx.Client) -> int:
    # The adapter's ffmpeg may still be warming up right after `up`; retry a few
    # times so a transient 502 (stream not yet published) doesn't fail the suite.
    created = None
    for _ in range(6):
        created = admin.post(
            "/api/v1/cameras", json={"name": "usbcam", "source_url": ADAPTER_SOURCE}
        )
        if created.status_code in (201, 409):
            break
        time.sleep(2)
    assert created is not None
    if created.status_code == 409:
        listing = admin.get("/api/v1/cameras").json()
        return int(next(c["id"] for c in listing if c["name"] == "usbcam"))
    assert created.status_code == 201, created.text
    body = created.json()
    # Registration proves the adapter's stream passed the real ffprobe contract.
    assert body["codec"] == "h264"
    return int(body["id"])


def test_adapter_stream_is_contract_conformant(admin: httpx.Client, adapter_camera_id: int) -> None:
    # The adapter's stream registers (H.264/<=1080p contract) and goes live via
    # go2rtc's internal RTSP re-serve — the same path native cameras take.
    online = False
    deadline = time.time() + 5
    while time.time() < deadline:
        if admin.get(f"/api/v1/cameras/{adapter_camera_id}").json()["online"]:
            online = True
            break
        time.sleep(0.5)
    assert online, "adapter camera not online within 5s"

    probe = _compose(
        "exec",
        "-T",
        "api",
        "ffprobe",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "csv=p=0",
        f"rtsp://go2rtc:8554/cam{adapter_camera_id}",
    )
    assert "h264" in probe.stdout, f"re-serve not readable: {probe.stdout!r} {probe.stderr!r}"


def test_adapter_webrtc_signaling_relay(admin: httpx.Client, adapter_camera_id: int) -> None:
    async def negotiate() -> None:
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        response = admin.post(
            f"/api/v1/cameras/{adapter_camera_id}/webrtc",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        assert response.status_code == 200, response.text
        assert "m=video" in response.text
        await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))
        await pc.close()

    asyncio.run(negotiate())


def test_adapter_container_hardened() -> None:
    cid = _compose("ps", "-q", "usb-adapter").stdout.strip()
    assert cid, "usb-adapter not running"
    out = subprocess.run(
        ["docker", "inspect", cid, "--format",
         "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    user, readonly, cap_drop = out.split("|")
    assert user and user not in ("root", "0", "0:0"), f"adapter runs as root ({user!r})"
    assert readonly == "true", "adapter rootfs is not read-only"
    assert "ALL" in cap_drop, f"adapter does not drop all capabilities ({cap_drop})"
