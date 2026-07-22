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

    # Host microphone (an ALSA/USB mic via the audio adapter). When set to the
    # adapter's RTSP URL (e.g. rtsp://eeper-audio-adapter:8554/mic), the api MERGES
    # this audio track into every camera's go2rtc stream — lighting up listen-in and
    # the sustained-sound nudge with no camera-native audio — AND registers it as a
    # standalone `mic` stream for a camera-independent "listen to the room". Empty =
    # no host mic (audio then comes only from a camera that carries its own track).
    audio_source_url: str = ""
    mic_stream_name: str = "mic"  # the go2rtc stream name for the standalone room-listen
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

    # Retention age policies (M4.3). Both default to 0 = disabled: the byte quota above
    # is the always-on media bound, and the raw telemetry is kept indefinitely unless an
    # operator opts in. When set, they add an AGE bound on top.
    #   - media_max_age_seconds: also evict finalized recording segments older than this
    #     (promoted clips are still never auto-evicted).
    #   - timeseries_retention_days: drop raw high-volume telemetry chunks
    #     (state_history, sensor_readings, pulseox_readings) older than this via a
    #     TimescaleDB retention policy. Derived/history tables (events, fused_states,
    #     sleep_sessions — the Tonight + trends sources) are always retained.
    media_max_age_seconds: int = 0
    timeseries_retention_days: int = 0

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
    mqtt_node: str = "insight"  # {node} in eeper/{node}/{motion,sound}|state/cam*/{state_type}
    # MQTT security (M3.1). When the broker is hardened, clients connect over TLS and
    # authenticate with a per-service credential (the insight engine uses the
    # `insight-publisher` account; the api uses `eeper-api`, which additionally holds
    # dynsec-provisioning + device-ingestion rights). Empty username keeps the legacy
    # anonymous/plaintext path (unit tests, pre-M3.1 stacks). mqtt_ca_cert is the MQTT
    # CA that verifies the broker's server certificate; TLS is implied when it is set.
    mqtt_tls_port: int = 8883
    mqtt_ca_cert: str = ""  # path to the MQTT CA cert; when set, connect over TLS on mqtt_tls_port
    mqtt_username: str = ""  # this service's dynsec client username ("" => anonymous)
    mqtt_password: str = ""
    # Artificial per-tick scorer slowdown (milliseconds) for the backpressure test;
    # 0 in production. Nonzero forces the frame-drop path without touching ffmpeg.
    insight_scorer_delay_ms: int = 0

    # Nudge worker (M2.4). The api-side worker reacts to insight nudge events
    # (LISTEN/NOTIFY + a reconciliation poll), auto-promotes a pre/post-roll clip, and
    # sends Web Push. Rate-limit + roll are delivery POLICY, so they live here (not in
    # the insight detector).
    nudge_pre_roll_seconds: int = 10
    nudge_post_roll_seconds: int = 15
    nudge_reconcile_interval_seconds: float = 5.0  # safety-net poll cadence
    nudge_reconcile_grace_seconds: float = 2.0  # only reconcile events older than this
    nudge_min_interval_seconds: float = 60.0  # min between nudges per camera (anti-spam)

    # Web Push / VAPID (M2.4). Generated by install.sh; empty disables push entirely
    # (the worker still auto-promotes clips + broadcasts). The subject is a mailto:
    # contact the push service can reach per the Web Push spec.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@eeper.local"

    # Insight engine (M2.3) — audio nudges.
    # Sound level is eeper's v1 audio nudge (a classic audio-monitor heuristic):
    # sustained loudness above the quiet nursery floor. Always on for audio cameras;
    # no model. Sensitivity 0..1 (higher = smaller elevation margin = more sensitive).
    sound_sensitivity: float = 0.5
    # Cry classification is EXPERIMENTAL and OFF by default: pretrained YAMNet can't
    # tell a cry from a bark / loud TV to a first-class bar, and M2.5's de-risk showed a
    # trained model can't either on the current corpus (first-class cry is the M2.6
    # corpus milestone). When enabled, the classifier ONNX is fetched + checksum-verified
    # from the models manifest at first run; an empty manifest path disables it (graceful
    # degradation — sound level still runs).
    cry_detection_enabled: bool = False
    cry_sensitivity: float = 0.5
    # Path to the models manifest (models/manifest.json) and a writable cache for the
    # fetched ONNX. Empty manifest => experimental cry stays off even if enabled
    # (the image ships no model; an operator opting in mounts the manifest + a cache).
    insight_models_manifest: str = ""
    insight_models_cache: str = "/tmp/eeper-models"  # noqa: S108 (ephemeral cache; re-fetch on restart)

    # Fusion worker (M3.3). The api-side worker derives sleep/wake + calm/distressed
    # from every extractor's persisted signals and writes fused_states transitions. It is
    # STATELESS: each cycle it re-runs the fusion over a warmup window (seeded from the
    # last persisted state), so a restart re-derives the current state from the durable
    # signals + transitions and sessions survive. Empty/false disables it entirely.
    fusion_enabled: bool = True
    fusion_interval_seconds: float = 30.0  # cycle cadence (one epoch)
    # The window re-run each cycle. Must be >> the state machine's sustain so the state
    # converges deterministically; also caps how far a cold start looks back.
    fusion_warmup_minutes: int = 45
    # Each cycle also materializes closed sleep sessions (the Trends source, M4.1) over
    # this lookback — long enough to catch a session that just closed, re-run idempotently.
    fusion_materialize_lookback_hours: int = 26

    # Pulse-oximetry (M4.2) is OPTIONAL and INSIGHTS-ONLY, and off by default. This flag
    # is the "profile enabled" half of the gate — the `pulseox` Compose profile sets it
    # true. Pulse-ox stays fully inert unless it is true AND an admin has acknowledged the
    # disclaimer (see eeper.api.pulseox_copy). eeper is never a vital-sign monitor.
    pulseox_profile_enabled: bool = False
    # Ingestion discards pulse-ox samples below this confidence — misleading readings are
    # dropped, never stored or fused (the discard rate is observable per device).
    pulseox_quality_threshold: float = 0.5


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
