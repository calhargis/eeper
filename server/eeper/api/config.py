"""Runtime configuration, loaded from ``EEPER_*`` environment variables.

No secrets have defaults: ``EEPER_DATABASE_URL`` and ``EEPER_SECRET_KEY`` are
required, so the service refuses to start without the values `install.sh`
generates. This is the code-level half of the "no default credentials" stance.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EEPER_", extra="ignore")

    # Required — no defaults on purpose (see module docstring).
    database_url: str = Field(min_length=1)
    secret_key: str = Field(min_length=16)

    # Auth cookies (httpOnly, Secure, SameSite=Lax). The refresh cookie is
    # path-scoped so it is only sent to the auth endpoints.
    access_cookie_name: str = "eeper_access"
    refresh_cookie_name: str = "eeper_refresh"
    refresh_cookie_path: str = "/api/v1/auth"

    # Token lifetimes.
    access_ttl_seconds: int = 15 * 60  # 15 minutes
    refresh_ttl_seconds: int = 30 * 24 * 60 * 60  # 30 days
    totp_challenge_ttl_seconds: int = 5 * 60  # 5 minutes

    # First-boot admin password policy.
    min_password_length: int = 12

    # TOTP.
    totp_issuer: str = "eeper"

    # Brute-force lockout: after N failed logins, lock the account for a window.
    max_failed_logins: int = 5
    lockout_seconds: int = 60

    # Media gateway (go2rtc). RTSP is derived from the same host for internal probes.
    go2rtc_url: str = "http://go2rtc:1984"
    go2rtc_rtsp_url: str = "rtsp://go2rtc:8554"
    # Contract: H.264, <=1080p (orientation-agnostic short/long-edge budget).
    max_video_short_edge: int = 1080
    max_video_long_edge: int = 1920
    probe_timeout_seconds: float = 6.0
    # Background camera health/keep-warm probe cadence.
    health_interval_seconds: float = 3.0

    # Recorder (M1.4). The recorder container writes segments under media_root; the
    # api reads them + writes promoted clips. Segments ring-buffer under a byte
    # quota; promoted clips live in a separate subtree and are never evicted.
    media_root: str = "/media"
    segment_seconds: int = 10
    media_quota_bytes: int = 10 * 1024**3  # 10 GiB recording ring buffer
    retention_interval_seconds: float = 30.0
    clip_max_seconds: int = 3600  # cap a single clip promotion (disk/DoS bound)

    # Insight engine (M2.1). When set (test overlay only), the audio stage writes
    # the newest 16 kHz mono PCM window per camera as a WAV here for the pipeline
    # test to read; empty in production (no tap). M2.2 also writes a per-camera
    # motion JSON (score/level/freshness) here when set.
    insight_tap_dir: str = ""

    # Insight engine (M2.2). MQTT event bus for movement-level state + motion
    # samples. Empty host disables MQTT entirely — the engine still samples,
    # scores, and writes state_history (graceful degradation). The broker is
    # internal-only (no host port); TLS + per-device ACLs land in M3.1.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_node: str = "insight"  # the {node} segment in eeper/{node}/motion|state
    # Artificial per-tick scorer slowdown (milliseconds) for the backpressure test;
    # 0 in production. Nonzero forces the frame-drop path without touching ffmpeg.
    insight_scorer_delay_ms: int = 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
