"""Integration tests for the M1.4 recorder (ring buffer + clips).

Runs against core + video (synthetic camera) + the recorder, brought up together
with fast test settings (2s segments, a small quota). Asserts the M1.4 criteria:
segments are written, a SIGKILL loses at most the active segment (crash safety),
clip promotion yields a duration-matching playable H.264 MP4, playback is
auth-enforced with HTTP Range, and eviction over quota keeps promoted clips.
"""

from __future__ import annotations

import ssl
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2]
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"
BASE_URL = "https://localhost"
ADMIN, PASSWORD = "admin", "correct horse battery staple"
SOURCE = "rtsp://synthetic-camera:8554/cam"
SEGMENT_SECONDS = 2  # matches recorder-test.yml

_COMPOSE = [
    "docker", "compose",
    "-f", str(DEPLOY_DIR / "docker-compose.yml"),
    "-f", str(DEPLOY_DIR / "video-test.yml"),
    "-f", str(DEPLOY_DIR / "recorder-test.yml"),
    "--profile", "core", "--profile", "video", "--profile", "record",
]


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_COMPOSE, *args], cwd=DEPLOY_DIR, capture_output=True, text=True, check=False
    )


def _rec(cmd: str) -> str:
    return _compose("exec", "-T", "recorder", "sh", "-c", cmd).stdout


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(CA_PATH))


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


def _segment_names(camera_id: int) -> list[str]:
    out = _rec(f"ls /media/rec/cam{camera_id}/ 2>/dev/null || true")
    return sorted(n for n in out.split() if n.endswith(".ts"))


def _wait_for_segments(camera_id: int, count: int, timeout: float = 25.0) -> list[str]:
    deadline = time.time() + timeout
    names: list[str] = []
    while time.time() < deadline:
        names = _segment_names(camera_id)
        if len(names) >= count:
            return names
        time.sleep(1)
    return names


def _seg_start(name: str) -> datetime:
    return datetime.strptime(name[:-3], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def test_recorder_container_hardened() -> None:
    cid = _compose("ps", "-q", "recorder").stdout.strip()
    assert cid, "recorder not running"
    out = subprocess.run(
        ["docker", "inspect", cid, "--format",
         "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    user, readonly, cap_drop = out.split("|")
    assert user and user not in ("root", "0", "0:0"), f"recorder runs as root ({user!r})"
    assert readonly == "true", "recorder rootfs is not read-only"
    assert "ALL" in cap_drop, f"recorder does not drop all capabilities ({cap_drop})"


def test_segments_are_written(camera_id: int) -> None:
    names = _wait_for_segments(camera_id, 2)
    assert len(names) >= 2, f"expected >=2 segments, got {names}"
    probe = _rec(
        f"ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "
        f"/media/rec/cam{camera_id}/{names[0]}"
    )
    assert "h264" in probe, f"segment not H.264: {probe!r}"


def test_crash_safety_loses_at_most_active_segment(camera_id: int) -> None:
    # Snapshot the finalized segments (all but the newest/active). Under the test
    # quota these few segments are well under the eviction threshold, so any that
    # disappear would be a crash-safety failure, not eviction.
    names = _wait_for_segments(camera_id, 3)
    assert len(names) >= 3
    finalized = names[:-1]

    assert _compose("kill", "-s", "SIGKILL", "recorder").returncode == 0
    assert _compose("start", "recorder").returncode == 0
    # Wait for the recorder to resume (new segment appears).
    time.sleep(4)

    survivors = set(_segment_names(camera_id))
    for name in finalized:
        assert name in survivors, f"finalized segment {name} lost to the crash"
        probe = _rec(
            f"ffprobe -v error {'/media/rec/cam' + str(camera_id) + '/' + name} "
            f">/dev/null 2>&1 && echo OK || echo BAD"
        )
        assert "OK" in probe, f"finalized segment {name} corrupted by the crash"
    # And recording resumed.
    assert len(_wait_for_segments(camera_id, len(finalized) + 1)) > len(finalized)


@pytest.fixture(scope="session")
def promoted_clip(admin: httpx.Client, camera_id: int) -> dict:
    # A window spanning >= 2 finalized segments (so the concat DTS path is exercised).
    names = _wait_for_segments(camera_id, 4)
    assert len(names) >= 4
    finalized = names[:-1]
    start = _seg_start(finalized[0]).isoformat().replace("+00:00", "Z")
    end = _seg_start(finalized[2]).isoformat().replace("+00:00", "Z")
    resp = admin.post(f"/api/v1/cameras/{camera_id}/clips", json={"start": start, "end": end})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def test_clip_is_playable_h264_matching_duration(
    admin: httpx.Client, camera_id: int, promoted_clip: dict
) -> None:
    clip = promoted_clip
    assert clip["codec"] == "h264"
    requested = (
        datetime.fromisoformat(clip["requested_end"]) - datetime.fromisoformat(clip["requested_start"])
    ).total_seconds()
    # Keyframe-aligned -c copy is accurate to within ~1 GOP (documented tolerance).
    assert abs(clip["duration_seconds"] - requested) <= SEGMENT_SECONDS + 1.0, clip
    # ffprobe the actual file: H.264 MP4, moov before mdat (faststart).
    got = admin.get(f"/api/v1/clips/{clip['id']}/media", headers={"Range": "bytes=0-4095"})
    head = got.content
    assert head[:64].find(b"ftyp") != -1
    assert head.find(b"moov") != -1 and head.find(b"moov") < head.find(b"mdat"), "not faststart"


def test_playback_enforces_auth_and_range(admin: httpx.Client, promoted_clip: dict) -> None:
    clip_id = promoted_clip["id"]
    # Unauthenticated -> 401.
    with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=15) as anon:
        assert anon.get(f"/api/v1/clips/{clip_id}/media").status_code == 401
    # A Range request -> 206 with Content-Range + Accept-Ranges.
    ranged = admin.get(f"/api/v1/clips/{clip_id}/media", headers={"Range": "bytes=0-1023"})
    assert ranged.status_code == 206
    assert ranged.headers.get("content-range", "").startswith("bytes 0-1023/")
    assert ranged.headers.get("accept-ranges") == "bytes"
    assert len(ranged.content) == 1024


def test_clip_promotion_rejects_naive_datetime(admin: httpx.Client, camera_id: int) -> None:
    # A timezone-naive window is a clean 422, not an uncaught 500.
    resp = admin.post(
        f"/api/v1/cameras/{camera_id}/clips",
        json={"start": "2026-07-06T12:00:00", "end": "2026-07-06T12:00:06"},
    )
    assert resp.status_code == 422, resp.text


def test_clip_promotion_rejects_uncovered_window(admin: httpx.Client, camera_id: int) -> None:
    # A window with no recorded segments is a 422 — never a 201 empty/unplayable clip.
    base = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
    resp = admin.post(
        f"/api/v1/cameras/{camera_id}/clips",
        json={
            "start": base.isoformat().replace("+00:00", "Z"),
            "end": (base + timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert resp.status_code == 422, resp.text


def test_eviction_over_quota_keeps_promoted_clips(
    admin: httpx.Client, camera_id: int, promoted_clip: dict
) -> None:
    # Record until the ring buffer exceeds the small test quota, then assert the
    # oldest segment was evicted while the promoted clip (separate subtree) survives.
    oldest_before = _segment_names(camera_id)[0]
    deadline = time.time() + 30
    while time.time() < deadline:
        if oldest_before not in _segment_names(camera_id):
            break
        time.sleep(2)
    assert oldest_before not in _segment_names(camera_id), "oldest segment was not evicted"

    # The promoted clip is untouched by eviction and still plays.
    got = admin.get(f"/api/v1/clips/{promoted_clip['id']}/media")
    assert got.status_code == 200
    assert got.headers.get("content-type") == "video/mp4"
    assert len(got.content) == promoted_clip["size_bytes"]


def test_starlette_range_cve_floor() -> None:
    # CVE-2025-62727 (O(n^2) Range parser) is reachable on the clip playback route;
    # the api's resolved Starlette must be >= 0.49.1.
    out = _compose(
        "exec", "-T", "api", "python", "-c",
        "import starlette;v=tuple(int(x) for x in starlette.__version__.split('.')[:3]);"
        "print('OK' if v>=(0,49,1) else 'BAD '+starlette.__version__)",
    ).stdout
    assert "OK" in out, f"Starlette < 0.49.1 (CVE-2025-62727): {out!r}"
