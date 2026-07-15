"""Integration tests for M3.1 (MQTT device onboarding + ingestion).

Runs against the core stack (db + api + the hardened mqtt broker). A device is
paired over the API, then published AS that device from inside the broker container
(the broker has no host port) with the credential pairing minted. Asserts the reading
lands in ``sensor_readings``, the ACLs isolate devices, malformed/oversized messages
are dropped without disturbing ingestion, and the device reads online.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2]
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"
BASE_URL = os.environ.get("EEPER_TEST_URL", "https://localhost")
ADMIN, PASSWORD = "admin", "correct horse battery staple"

_COMPOSE = ["docker", "compose", "-f", str(DEPLOY_DIR / "docker-compose.yml"), "--profile", "core"]


def _pg_password() -> str:
    for line in (DEPLOY_DIR / ".env").read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("POSTGRES_PASSWORD not found in deploy/.env")


def _mqtt_pub(user: str, password: str, topic: str, payload: str) -> int:
    """Publish AS a device from inside the broker container. Returns the exit code
    (non-zero => connection/ACL refused, since a denied publish disconnects the client)."""
    return subprocess.run(
        [
            *_COMPOSE, "exec", "-T", "mqtt", "mosquitto_pub",
            "-h", "127.0.0.1", "-p", "8883",
            "--cafile", "/mosquitto/certs/mqtt-ca.crt",
            "-u", user, "-P", password, "-q", "1", "-t", topic, "-m", payload,
        ],
        cwd=DEPLOY_DIR, capture_output=True, text=True, check=False,
    ).returncode


def _reading_count(device_id: int) -> int:
    out = subprocess.run(
        [
            *_COMPOSE, "exec", "-T", "-e", f"PGPASSWORD={_pg_password()}", "db",
            "psql", "-U", "eeper", "-d", "eeper", "-tAc",
            f"SELECT count(*) FROM sensor_readings WHERE device_id = {device_id}",
        ],
        cwd=DEPLOY_DIR, capture_output=True, text=True, check=False,
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


def _reading(value: float = 0.5, quality: float = 0.9) -> str:
    return json.dumps({"ts": time.time(), "type": "movement", "value": value, "unit": "index", "quality": quality})


def _thermal_features(presence: bool = True) -> str:
    return json.dumps(
        {
            "ts": time.time(),
            "presence": presence,
            "presence_confidence": 0.7 if presence else 0.0,
            "warm_region_area": 0.1 if presence else 0.0,
            "warm_region_centroid": [0.5, 0.5] if presence else None,
        }
    )


def _thermal_count(device_id: int) -> int:
    out = subprocess.run(
        [
            *_COMPOSE, "exec", "-T", "-e", f"PGPASSWORD={_pg_password()}", "db",
            "psql", "-U", "eeper", "-d", "eeper", "-tAc",
            f"SELECT count(*) FROM thermal_features WHERE device_id = {device_id}",
        ],
        cwd=DEPLOY_DIR, capture_output=True, text=True, check=False,
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


@pytest.fixture(scope="session")
def admin() -> httpx.Client:
    assert CA_PATH.exists(), f"local CA not found at {CA_PATH}"
    ctx = ssl.create_default_context(cafile=str(CA_PATH))
    with httpx.Client(base_url=BASE_URL, verify=ctx, timeout=20) as client:
        for _ in range(60):
            try:
                if client.get("/api/v1/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(2)
        created = client.post("/api/v1/system/first-boot", json={"username": ADMIN, "password": PASSWORD})
        if created.status_code == 409:
            assert client.post("/api/v1/auth/login", json={"username": ADMIN, "password": PASSWORD}).status_code == 200
        else:
            assert created.status_code == 201, created.text
        yield client


def _pair(admin: httpx.Client, name: str, kind: str = "mmwave") -> dict:
    resp = admin.post("/api/v1/devices", json={"name": name, "kind": kind})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mqtt_password"] and body["mqtt_username"] == f"dev-{body['id']}"
    return body


def test_pair_publish_lands_in_sensor_readings(admin: httpx.Client) -> None:
    dev = _pair(admin, "mmwave-crib")
    did = dev["id"]
    assert _reading_count(did) == 0
    assert _mqtt_pub(dev["mqtt_username"], dev["mqtt_password"], f"eeper/dev/{did}/movement", _reading()) == 0
    # ingestion is async (batched ~1 s); poll for the row + the online flip.
    for _ in range(30):
        if _reading_count(did) >= 1:
            break
        time.sleep(1)
    assert _reading_count(did) >= 1, "reading never landed in sensor_readings"
    online = next(d for d in admin.get("/api/v1/devices").json() if d["id"] == did)["online"]
    assert online is True


def test_device_acl_isolation(admin: httpx.Client) -> None:
    a = _pair(admin, "node-a")
    b = _pair(admin, "node-b")
    before = _reading_count(b["id"])
    # A publishes into B's subtree and into the internal insight topics — both ACL-denied.
    _mqtt_pub(a["mqtt_username"], a["mqtt_password"], f"eeper/dev/{b['id']}/movement", _reading(value=9.9))
    _mqtt_pub(a["mqtt_username"], a["mqtt_password"], "eeper/insight/state/cam0/cry", "LEAK")
    time.sleep(3)
    assert _reading_count(b["id"]) == before, "device A's write into B's subtree was not blocked"


def test_malformed_and_oversized_are_dropped_without_disturbing_ingestion(admin: httpx.Client) -> None:
    dev = _pair(admin, "fuzz-node")
    did, u, pw = dev["id"], dev["mqtt_username"], dev["mqtt_password"]
    _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", "{not json")  # malformed
    _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", json.dumps({"ts": 1, "value": 1}))  # missing fields
    _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", "x" * 5000)  # oversized
    time.sleep(3)
    assert _reading_count(did) == 0, "a malformed/oversized message was ingested"
    # ingestion survived: the api is healthy and a subsequent valid reading still lands.
    assert admin.get("/api/v1/health").status_code == 200
    assert _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", _reading()) == 0
    for _ in range(30):
        if _reading_count(did) >= 1:
            break
        time.sleep(1)
    assert _reading_count(did) >= 1, "ingestion did not recover after malformed input"


def test_unpair_revokes_the_credential(admin: httpx.Client) -> None:
    dev = _pair(admin, "temp-node")
    did, u, pw = dev["id"], dev["mqtt_username"], dev["mqtt_password"]
    assert _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", _reading()) == 0
    assert admin.delete(f"/api/v1/devices/{did}").status_code == 200
    time.sleep(1)
    # The revoked account can no longer connect (non-zero exit).
    assert _mqtt_pub(u, pw, f"eeper/dev/{did}/movement", _reading()) != 0


def test_thermal_node_pairs_publishes_features_and_isolates(admin: httpx.Client) -> None:
    # M6.1 pairing parity: a thermal node pairs, publishes, and revokes through the exact
    # M3.1 flow (no special-casing), and its features land in thermal_features + it reads
    # online. The M3.1 ACL still isolates it from other devices' subtrees.
    dev = _pair(admin, "thermal-crib", kind="thermal")
    did, u, pw = dev["id"], dev["mqtt_username"], dev["mqtt_password"]
    other = _pair(admin, "thermal-other", kind="thermal")

    assert _thermal_count(did) == 0
    assert _mqtt_pub(u, pw, f"eeper/dev/{did}/thermal_features", _thermal_features()) == 0
    for _ in range(30):
        if _thermal_count(did) >= 1:
            break
        time.sleep(1)
    assert _thermal_count(did) >= 1, "thermal features never landed in thermal_features"
    online = next(d for d in admin.get("/api/v1/devices").json() if d["id"] == did)["online"]
    assert online is True

    # A thermal node cannot publish into another device's subtree (ACL isolation holds).
    before = _thermal_count(other["id"])
    _mqtt_pub(u, pw, f"eeper/dev/{other['id']}/thermal_features", _thermal_features())
    time.sleep(3)
    assert _thermal_count(other["id"]) == before

    # Unpair revokes the credential (same M3.1 flow as any sensor node).
    assert admin.delete(f"/api/v1/devices/{did}").status_code == 200
    time.sleep(1)
    assert _mqtt_pub(u, pw, f"eeper/dev/{did}/thermal_features", _thermal_features()) != 0
