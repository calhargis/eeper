"""Integration tests for M1.1 (media gateway + RTSP contract).

Runs against a stack brought up with the `video` profile + the `video-test`
overlay (core + go2rtc + a synthetic camera on a shared network). The CI `video`
job brings it up, runs this suite, and tears it down.
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
SOURCE_H264 = "rtsp://synthetic-camera:8554/cam"
SOURCE_H265 = "rtsp://synthetic-camera:8554/cam-h265"

_COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(DEPLOY_DIR / "docker-compose.yml"),
    "-f",
    str(DEPLOY_DIR / "video-test.yml"),
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
        # Fresh stack -> first-boot; re-run against an existing stack -> log in.
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
    created = admin.post(
        "/api/v1/cameras", json={"name": "nursery", "source_url": SOURCE_H264}
    )
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


def _online(admin: httpx.Client, camera_id: int) -> bool:
    return bool(admin.get(f"/api/v1/cameras/{camera_id}").json()["online"])


def test_register_h264_available_within_5s(admin: httpx.Client, camera_id: int) -> None:
    # Criterion 1: the registered stream is available via internal RTSP + WebRTC.
    deadline = time.time() + 5
    while time.time() < deadline and not _online(admin, camera_id):
        time.sleep(0.5)
    assert _online(admin, camera_id), "camera not online within 5s"

    # Internal RTSP re-serve is readable (probed from inside the api container).
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
        f"rtsp://go2rtc:8554/cam{camera_id}",
    )
    assert "h264" in probe.stdout, (
        f"RTSP re-serve not readable: {probe.stdout!r} {probe.stderr!r}"
    )


def test_register_h265_rejected_with_actionable_error(admin: httpx.Client) -> None:
    # Criterion 2: an unsupported codec is rejected with an actionable message.
    rejected = admin.post(
        "/api/v1/cameras", json={"name": "bad", "source_url": SOURCE_H265}
    )
    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert "H.264" in detail and "hevc" in detail.lower()


def test_source_url_is_never_exposed(admin: httpx.Client, camera_id: int) -> None:
    # Camera source URLs embed credentials + the internal IP; they must not be
    # echoed back to any client (not even the admin who registered them).
    detail = admin.get(f"/api/v1/cameras/{camera_id}").json()
    assert "source_url" not in detail, detail
    listing = admin.get("/api/v1/cameras").json()
    assert all("source_url" not in c for c in listing), listing


def test_non_rtsp_source_is_rejected(admin: httpx.Client) -> None:
    # The source is handed to ffprobe/go2rtc verbatim; only rtsp(s):// is accepted,
    # closing off file:// reads / http:// SSRF / option injection.
    for bad in ("file:///etc/hostname", "http://169.254.169.254/latest/meta-data/", "-version"):
        rejected = admin.post("/api/v1/cameras", json={"name": "x", "source_url": bad})
        assert rejected.status_code == 422, (bad, rejected.text)
        assert "rtsp" in rejected.text.lower()


def test_duplicate_source_is_rejected(admin: httpx.Client, camera_id: int) -> None:
    # The same source can't be registered twice within a household.
    dup = admin.post("/api/v1/cameras", json={"name": "dup", "source_url": SOURCE_H264})
    assert dup.status_code == 409, dup.text


def test_webrtc_signaling_relay_returns_valid_answer(
    admin: httpx.Client, camera_id: int
) -> None:
    # Criterion 1/4: the api relay returns a valid SDP answer for the stream.
    async def negotiate() -> None:
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())
        response = admin.post(
            f"/api/v1/cameras/{camera_id}/webrtc",
            content=pc.localDescription.sdp,
            headers={"Content-Type": "application/sdp"},
        )
        assert response.status_code == 200, response.text
        assert "application/sdp" in response.headers.get("content-type", "")
        assert "m=video" in response.text
        # A real client parses the answer; this proves it is well-formed.
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=response.text, type="answer")
        )
        await pc.close()

    asyncio.run(negotiate())


def test_webrtc_answer_offers_opus_audio(admin: httpx.Client, camera_id: int) -> None:
    # M2.1 listen-in (deterministic server-side guard): the stream is registered
    # with a second ffmpeg source that transcodes audio to Opus (AAC isn't a
    # WebRTC audio codec), so an offer with an audio transceiver gets an m=audio
    # answer advertising opus.
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
        sdp = response.text
        assert "m=audio" in sdp, "no audio m-line — go2rtc Opus source not registered"
        assert "opus" in sdp.lower(), "audio present but no opus rtpmap"
        await pc.close()

    asyncio.run(negotiate())


def test_webrtc_relay_requires_auth(camera_id: int) -> None:
    # Criterion 4: signaling is only reachable authenticated, through the relay.
    with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=10) as anon:
        response = anon.post(
            f"/api/v1/cameras/{camera_id}/webrtc",
            content="v=0",
            headers={"Content-Type": "application/sdp"},
        )
    assert response.status_code == 401


def test_gateway_control_planes_are_not_reachable(camera_id: int) -> None:
    # M1.2 scoped isolation invariant: the ONLY published go2rtc port is the WebRTC
    # media transport (8555, tcp+udp) — a deliberate, minimal regression of M1.1's
    # zero-ports stance so the browser can reach media. The signaling REST (:1984)
    # and RTSP re-serve (:8554) — the ffprobe/credential/stream-management surface
    # that actually embodies the isolation stance — stay UNPUBLISHED and dark off
    # the network. Signaling still flows only through the authenticated api relay.
    cid = _compose("ps", "-q", "go2rtc").stdout.strip()
    assert cid, "go2rtc not running"
    # `docker port` lists one line per published container port, e.g.
    # "8555/tcp -> 127.0.0.1:8555".
    published = subprocess.run(["docker", "port", cid], capture_output=True, text=True).stdout
    lines = [line for line in published.splitlines() if "->" in line]
    exposed = {line.split(" ->", 1)[0].strip() for line in lines}
    assert exposed == {"8555/tcp", "8555/udp"}, (
        f"go2rtc must publish ONLY the media port; got {sorted(exposed)} "
        "(the :1984 signaling and :8554 RTSP control planes must stay unpublished)"
    )
    # And it must bind to a specific interface, never a wildcard — binding 8555 to
    # 0.0.0.0/:: would expose media on every interface (incl. any public one),
    # defeating the point of EEPER_GO2RTC_CANDIDATE.
    host_ips = {line.split("->", 1)[1].strip().rsplit(":", 1)[0].strip("[]") for line in lines}
    assert host_ips.isdisjoint({"0.0.0.0", "::"}), (
        f"go2rtc media port must bind a specific interface, not a wildcard; got {sorted(host_ips)}"
    )

    # And the control planes stay unreachable from a foreign docker network.
    network = f"eeper-foreign-{os.getpid()}"
    subprocess.run(
        ["docker", "network", "create", network], capture_output=True, check=False
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "curlimages/curl:latest",
                "-sS",
                "--max-time",
                "4",
                "http://go2rtc:1984/api",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, "go2rtc reachable from a foreign network"
    finally:
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, check=False
        )


def test_gateway_container_hardened() -> None:
    cid = _compose("ps", "-q", "go2rtc").stdout.strip()
    assert cid, "go2rtc not running"
    out = subprocess.run(
        ["docker", "inspect", cid, "--format",
         "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    user, readonly, cap_drop = out.split("|")
    assert user and user not in ("root", "0", "0:0"), f"go2rtc runs as root ({user!r})"
    assert readonly == "true", "go2rtc rootfs is not read-only"
    assert "ALL" in cap_drop, f"go2rtc does not drop all capabilities ({cap_drop})"


def test_resilience_offline_then_recovery(admin: httpx.Client, camera_id: int) -> None:
    # Criterion 3: killing the camera flips health offline; restarting recovers
    # within 15 s. (Runs last — it cycles the synthetic camera.)
    _compose("stop", "synthetic-camera")
    off_deadline = time.time() + 15
    while time.time() < off_deadline and _online(admin, camera_id):
        time.sleep(1)
    assert not _online(admin, camera_id), (
        "health did not go offline after the camera stopped"
    )

    _compose("start", "synthetic-camera")
    on_deadline = time.time() + 15
    while time.time() < on_deadline and not _online(admin, camera_id):
        time.sleep(1)
    assert _online(admin, camera_id), "stream did not recover within 15s"
