"""Shared fixtures for the M2.2 motion-pipeline integration tests.

Runs against core + video (synthetic camera) + insight + the internal MQTT broker.
Because the broker has no host port (internal-only), MQTT is read from INSIDE the
broker container; state_history is read from INSIDE the db container. Latency is
measured from insight-internal timestamps (the motion tap's ts and the DB row's
ts), so docker-exec overhead never contaminates the 2 s budget.

Helpers are exposed via the ``stack`` fixture (not module imports) so each test
file stays self-contained, matching the other deploy test suites.
"""

from __future__ import annotations

import json
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2]
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"
BASE_URL = "https://localhost"
ADMIN, PASSWORD = "admin", "correct horse battery staple"

MOTION_SOURCE = "rtsp://synthetic-camera:8554/cam-motion"
NOAUDIO_SOURCE = "rtsp://synthetic-camera:8554/cam-noaudio"
SOUND_SOURCE = "rtsp://synthetic-camera:8554/cam-sound"

COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(DEPLOY_DIR / "docker-compose.yml"),
    "-f",
    str(DEPLOY_DIR / "video-test.yml"),
    "-f",
    str(DEPLOY_DIR / "insight-test.yml"),
    "--profile",
    "core",
    "--profile",
    "video",
    "--profile",
    "insight",
]


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(CA_PATH))


def _pg_password() -> str:
    for line in (DEPLOY_DIR / ".env").read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("POSTGRES_PASSWORD not found in deploy/.env")


class Stack:
    """Thin helpers that reach into the running compose stack."""

    COMPOSE = COMPOSE
    DEPLOY_DIR = DEPLOY_DIR

    def read_motion(self, camera_id: int) -> dict | None:
        """The insight motion tap JSON for a camera (atomic write -> never partial);
        None until the first window is scored."""
        result = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "insight",
                "cat",
                f"/run/insight/cam{camera_id}.motion.json",
            ],
            cwd=DEPLOY_DIR,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def latest_state(
        self, camera_id: int, state_type: str | None = None
    ) -> tuple[float, str] | None:
        """Newest state_history row for a camera as (ts_epoch, value), or None.
        Optionally filtered to one state_type (movement_level / sound_level / cry)."""
        where = f"camera_id = {camera_id}"
        if state_type is not None:
            where += f" AND state_type = '{state_type}'"
        sql = (
            "SELECT extract(epoch from ts), value FROM state_history "
            f"WHERE {where} ORDER BY ts DESC LIMIT 1"
        )
        result = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "-e",
                f"PGPASSWORD={_pg_password()}",
                "db",
                "psql",
                "-U",
                "eeper",
                "-d",
                "eeper",
                "-tAF,",
                "-c",
                sql,
            ],
            cwd=DEPLOY_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        out = result.stdout.strip()
        if result.returncode != 0 or not out:
            return None
        ts_str, _, value = out.partition(",")
        return float(ts_str), value

    def mqtt_retained(self, topic: str, timeout_s: int = 3) -> dict | None:
        """The retained message on a topic, read from inside the internal broker."""
        result = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "mqtt",
                "mosquitto_sub",
                "-h",
                "127.0.0.1",
                "-t",
                topic,
                "-C",
                "1",
                "-W",
                str(timeout_s),
            ],
            cwd=DEPLOY_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        out = result.stdout.strip()
        if not out:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None

    def container_id(self, service: str) -> str:
        return subprocess.run(
            [*COMPOSE, "ps", "-q", service],
            cwd=DEPLOY_DIR,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()


@pytest.fixture(scope="session")
def stack() -> Stack:
    return Stack()


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
            client.post(
                "/api/v1/auth/login", json={"username": ADMIN, "password": PASSWORD}
            )
        yield client


def _register(admin: httpx.Client, name: str, source: str) -> dict:
    created = admin.post("/api/v1/cameras", json={"name": name, "source_url": source})
    if created.status_code == 409:
        # 409 == the source_url is already registered (it's unique). We can only recover
        # by name, since CameraOut omits source_url (it embeds credentials) — so a source
        # registered under a *different* name is unrecoverable. Fail loudly with the
        # listing rather than an opaque StopIteration if that ever happens.
        listing = admin.get("/api/v1/cameras").json()
        existing = next((c for c in listing if c["name"] == name), None)
        assert existing is not None, (
            f"409 registering {name!r} ({source}) but no camera is named {name!r}; "
            f"the source is likely registered under another name. Cameras: "
            f"{[c['name'] for c in listing]}"
        )
        return existing
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="session")
def motion_camera(admin: httpx.Client) -> dict:
    return _register(admin, "motion", MOTION_SOURCE)


@pytest.fixture(scope="session")
def noaudio_camera(admin: httpx.Client) -> dict:
    return _register(admin, "noaudio", NOAUDIO_SOURCE)


@pytest.fixture(scope="session")
def sound_camera(admin: httpx.Client) -> dict:
    return _register(admin, "sound", SOUND_SOURCE)
