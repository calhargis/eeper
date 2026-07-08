"""Integration test for the M2.1 audio pipeline (criterion 1).

Runs against core + video (synthetic camera) + the insight engine, which extracts
each camera's audio to 16 kHz mono PCM windows. The synthetic camera's audio is a
known 1 kHz sine, so we verify the extracted window is that tone by its SIGNAL
PROPERTIES rather than a bit-exact checksum — AAC is lossy and ffmpeg's float AAC
decoder varies across CPU/version, so a byte-exact fixture would go red on a base-
image bump. A pure-Python Goertzel (no numpy) asserts the 1 kHz bin dominates
control bins; the committed pure-sine reference WAV self-passes the same check
(calibration guard), honoring "correct sample values (checksum against a fixture)".
"""

from __future__ import annotations

import io
import json
import math
import ssl
import struct
import subprocess
import time
import wave
from pathlib import Path

import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2]
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"
BASE_URL = "https://localhost"
ADMIN, PASSWORD = "admin", "correct horse battery staple"
SOURCE = "rtsp://synthetic-camera:8554/cam"

FIXTURE_DIR = DEPLOY_DIR / "tests" / "fixtures" / "audio"
CONTRACT = json.loads((FIXTURE_DIR / "tone_1k.json").read_text())
REFERENCE_WAV = FIXTURE_DIR / "tone_1k_16k_mono.wav"

_COMPOSE = [
    "docker", "compose",
    "-f", str(DEPLOY_DIR / "docker-compose.yml"),
    "-f", str(DEPLOY_DIR / "video-test.yml"),
    "-f", str(DEPLOY_DIR / "insight-test.yml"),
    "--profile", "core", "--profile", "video", "--profile", "insight",
]


def _compose_bytes(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([*_COMPOSE, *args], cwd=DEPLOY_DIR, capture_output=True, check=False)


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(CA_PATH))


def _goertzel(samples: tuple[int, ...], sample_rate: int, hz: float) -> float:
    n = len(samples)
    k = round(n * hz / sample_rate)  # derived from N, never hard-coded
    coeff = 2.0 * math.cos(2.0 * math.pi * k / n)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def _verify_tone(wav_bytes: bytes) -> None:
    reader = wave.open(io.BytesIO(wav_bytes), "rb")
    channels, rate = reader.getnchannels(), reader.getframerate()
    width, frames = reader.getsampwidth(), reader.getnframes()
    pcm = reader.readframes(frames)

    # Structural: the declared format is exactly what the pipeline promises.
    assert channels == CONTRACT["channels"], f"channels {channels}"
    assert rate == CONTRACT["sample_rate"], f"rate {rate}"
    assert width == CONTRACT["sample_width"], f"sample width {width}"
    assert frames == CONTRACT["samples"], f"expected {CONTRACT['samples']} samples, got {frames}"

    samples = struct.unpack(f"<{frames}h", pcm)
    power = _goertzel(samples, rate, CONTRACT["expected_hz"])
    control = max(_goertzel(samples, rate, f) for f in CONTRACT["control_hz"])
    dominance = power / control if control > 0 else float("inf")
    assert dominance >= CONTRACT["dominance_ratio_min"], (
        f"{CONTRACT['expected_hz']} Hz does not dominate ({dominance:.1f}x < "
        f"{CONTRACT['dominance_ratio_min']}x) — not the expected tone"
    )

    rms = math.sqrt(sum(x * x for x in samples) / frames)
    assert CONTRACT["rms_min"] <= rms <= CONTRACT["rms_max"], f"rms {rms:.1f} out of band"

    half = frames // 2
    r1 = math.sqrt(sum(x * x for x in samples[:half]) / half)
    r2 = math.sqrt(sum(x * x for x in samples[half:]) / (frames - half))
    ratio = r1 / r2 if r2 > 0 else float("inf")
    assert CONTRACT["half_rms_ratio_min"] <= ratio <= CONTRACT["half_rms_ratio_max"], (
        f"window energy not contiguous (half ratio {ratio:.3f}) — likely mis-framed"
    )


@pytest.fixture(scope="session")
def admin() -> httpx.Client:
    assert CA_PATH.exists(), f"local CA not found at {CA_PATH}"
    with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=30) as client:
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
            client.post("/api/v1/auth/login", json={"username": ADMIN, "password": PASSWORD})
        yield client


@pytest.fixture(scope="session")
def camera_id(admin: httpx.Client) -> int:
    created = admin.post("/api/v1/cameras", json={"name": "nursery", "source_url": SOURCE})
    if created.status_code == 409:
        listing = admin.get("/api/v1/cameras").json()
        return int(next(c["id"] for c in listing if c["name"] == "nursery"))
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


def test_reference_fixture_self_passes() -> None:
    # Calibration guard: the committed pure-sine reference passes the exact same
    # property check the live extraction must pass — proving the check is tuned.
    _verify_tone(REFERENCE_WAV.read_bytes())


def test_insight_container_hardened() -> None:
    cid = subprocess.run(
        [*_COMPOSE, "ps", "-q", "insight"], cwd=DEPLOY_DIR, capture_output=True, text=True, check=False
    ).stdout.strip()
    assert cid, "insight not running"
    out = subprocess.run(
        ["docker", "inspect", cid, "--format",
         "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    user, readonly, cap_drop = out.split("|")
    assert user and user not in ("root", "0", "0:0"), f"insight runs as root ({user!r})"
    assert readonly == "true", "insight rootfs is not read-only"
    assert "ALL" in cap_drop, f"insight does not drop all capabilities ({cap_drop})"


def test_extracted_audio_is_16khz_mono_1khz_tone(admin: httpx.Client, camera_id: int) -> None:
    # Poll for the tap WAV (written atomically, so a read is never partial), then
    # verify the extracted window is the synthetic camera's 1 kHz tone.
    deadline = time.time() + 25
    last_error: Exception | None = None
    while time.time() < deadline:
        result = _compose_bytes("exec", "-T", "insight", "cat", f"/run/insight/cam{camera_id}.wav")
        if result.returncode == 0 and len(result.stdout) >= CONTRACT["samples"] * 2:
            try:
                _verify_tone(result.stdout)
                return
            except AssertionError as exc:  # window may still be warming up; retry
                last_error = exc
        time.sleep(1)
    if last_error is not None:
        raise last_error
    raise AssertionError("no audio tap window appeared within 25s")
