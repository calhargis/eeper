"""Integration tests for the M0.2 core stack.

These run against a stack already brought up by ``deploy/install.sh`` (that is
the thing under test — a clean-host install). The CI job in
``.github/workflows/stack.yml`` runs install.sh, then this suite, then tears
down. Tests are ordered: fresh-state and state-independent assertions first, the
state-mutating first-boot flow last.

Config via env (defaults suit a local ``install.sh`` run):
  EEPER_TEST_DOMAIN=localhost  EEPER_TEST_HTTP_PORT=80  EEPER_TEST_HTTPS_PORT=443
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1]
DOMAIN = os.environ.get("EEPER_TEST_DOMAIN", "localhost")
HTTP_PORT = os.environ.get("EEPER_TEST_HTTP_PORT", "80")
HTTPS_PORT = os.environ.get("EEPER_TEST_HTTPS_PORT", "443")
CA_PATH = DEPLOY_DIR / "eeper-local-ca.crt"

_https = "" if HTTPS_PORT == "443" else f":{HTTPS_PORT}"
_http = "" if HTTP_PORT == "80" else f":{HTTP_PORT}"
BASE_URL = f"https://{DOMAIN}{_https}"
HTTP_URL = f"http://{DOMAIN}{_http}"

BASIC_AUTH_DENYLIST = {
    "changeme",
    "password",
    "admin",
    "postgres",
    "secret",
    "eeper",
    "default",
    "",
}
ADMIN_PW = "correct horse battery staple"


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(CA_PATH))


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "--profile", "core", *args],
        cwd=DEPLOY_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def _container_id(service: str) -> str:
    return _compose("ps", "-q", service).stdout.strip()


def _published_ports(service: str) -> str:
    cid = _container_id(service)
    if not cid:
        return ""
    return subprocess.run(
        ["docker", "port", cid], capture_output=True, text=True, check=False
    ).stdout.strip()


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    assert CA_PATH.exists(), f"local CA not found at {CA_PATH} — did install.sh run?"
    with httpx.Client(
        base_url=BASE_URL, verify=_ssl_ctx(), follow_redirects=False, timeout=10
    ) as c:
        for _ in range(60):
            try:
                if c.get("/api/v1/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(2)
        else:
            pytest.fail("stack did not become ready in time")
        yield c


# ── fresh-state assertions (no admin exists yet) ────────────────────────────


def test_no_default_credentials_in_env() -> None:
    env = (DEPLOY_DIR / ".env").read_text()
    values = dict(
        line.split("=", 1)
        for line in env.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    for key in ("POSTGRES_PASSWORD", "EEPER_SECRET_KEY"):
        value = values.get(key, "")
        assert len(value) >= 32, f"{key} is too short to be a generated secret"
        assert re.fullmatch(r"[0-9a-f]+", value), f"{key} is not random hex"
        assert value.lower() not in BASIC_AUTH_DENYLIST, f"{key} looks like a default"
    # Tracked config must never contain the real secret values.
    for name in ("docker-compose.yml", "caddy/Caddyfile", ".env.example"):
        text = (DEPLOY_DIR / name).read_text()
        assert values["POSTGRES_PASSWORD"] not in text
        assert values["EEPER_SECRET_KEY"] not in text


def test_database_has_no_default_user_rows() -> None:
    result = _compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "eeper",
        "-d",
        "eeper",
        "-tAc",
        "select count(*) from users",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0", (
        f"expected 0 users on a fresh install, got {result.stdout!r}"
    )


def test_first_boot_required_on_fresh_install(client: httpx.Client) -> None:
    assert client.get("/api/v1/system/status").json()["first_boot_required"] is True


def test_protected_endpoint_401_before_wizard(client: httpx.Client) -> None:
    assert client.get("/api/v1/me").status_code == 401


# ── state-independent assertions ────────────────────────────────────────────


def test_http_redirects_to_https() -> None:
    with httpx.Client(verify=_ssl_ctx(), follow_redirects=False, timeout=10) as c:
        response = c.get(f"{HTTP_URL}/")
    assert response.status_code in (301, 308)
    assert response.headers["location"].startswith("https://")


def test_tls_chains_to_local_ca_with_headers(client: httpx.Client) -> None:
    # The `client` fixture verifies against the extracted local CA (verify=CA);
    # a successful 200 proves the served cert chains to it.
    response = client.get("/")
    assert response.status_code == 200
    headers = {k.lower() for k in response.headers}
    for header in (
        "strict-transport-security",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "content-security-policy",
    ):
        assert header in headers, f"missing security header: {header}"


def test_only_caddy_publishes_ports() -> None:
    # Guard against a false pass: an absent container would also report no ports.
    for service in ("api", "db", "caddy"):
        assert _container_id(service), f"{service} is not running"
    assert _published_ports("api") == "", "api must not publish host ports"
    assert _published_ports("db") == "", "db must not publish host ports"
    assert _published_ports("caddy") != "", "caddy should publish the edge ports"


def test_all_containers_hardened() -> None:
    for service in ("db", "api", "web", "caddy"):
        cid = _container_id(service)
        assert cid, f"{service} is not running"
        fmt = (
            "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}"
            "|{{json .HostConfig.CapDrop}}|{{json .HostConfig.SecurityOpt}}"
        )
        out = subprocess.run(
            ["docker", "inspect", cid, "--format", fmt],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        user, readonly, cap_drop, sec_opt = out.split("|")
        assert user and user not in ("root", "0", "0:0"), (
            f"{service} runs as root ({user!r})"
        )
        assert readonly == "true", f"{service} root filesystem is not read-only"
        assert "ALL" in cap_drop, (
            f"{service} does not drop all capabilities ({cap_drop})"
        )
        assert "no-new-privileges" in sec_opt, (
            f"{service} is missing no-new-privileges ({sec_opt})"
        )


# ── state-mutating flow (runs last) ─────────────────────────────────────────


def test_first_boot_is_race_safe_then_login_logout() -> None:
    # Fire concurrent first-boot attempts with DISTINCT usernames. Exactly one
    # must win (201) and the rest must be refused (409); more than one 201 means
    # the admin_exists check-then-insert raced two admins into existence.
    usernames = [f"admin{i}" for i in range(5)]

    def attempt(name: str) -> tuple[str, int]:
        with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=15) as c:
            r = c.post(
                "/api/v1/system/first-boot",
                json={"username": name, "password": ADMIN_PW},
            )
            return name, r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(usernames)) as pool:
        results = list(pool.map(attempt, usernames))

    created = [name for name, code in results if code == 201]
    assert len(created) == 1, f"expected exactly one admin, got {results}"
    winner = created[0]
    assert all(code == 409 for name, code in results if name != winner), results

    # Login / logout / bad-login with the winning admin.
    with httpx.Client(base_url=BASE_URL, verify=_ssl_ctx(), timeout=10) as c:
        assert (
            c.post(
                "/api/v1/auth/login", json={"username": winner, "password": ADMIN_PW}
            ).status_code
            == 200
        )
        assert c.get("/api/v1/me").status_code == 200

        c.post("/api/v1/auth/logout")
        assert c.get("/api/v1/me").status_code == 401, (
            "must be unauthenticated after logout"
        )

        assert (
            c.post(
                "/api/v1/auth/login", json={"username": winner, "password": "wrong"}
            ).status_code
            == 401
        )
        assert c.get("/api/v1/system/status").json()["first_boot_required"] is False
