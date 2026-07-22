"""Request/response models for the API."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Role = Literal["admin", "viewer"]

# Cap password length so an oversized input can't turn Argon2 hashing into a DoS.
_MAX_PASSWORD = 1024


class SystemStatus(BaseModel):
    first_boot_required: bool
    version: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class MessageOut(BaseModel):
    detail: str


# ── auth ────────────────────────────────────────────────────────────────────


class FirstBootRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    # Minimum length is enforced against settings in the handler.
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class LoginResult(BaseModel):
    """Either an authenticated user (tokens set via cookies) or a TOTP challenge."""

    totp_required: bool = False
    challenge: str | None = None
    user: UserOut | None = None


class TotpVerifyRequest(BaseModel):
    challenge: str
    code: str = Field(min_length=6, max_length=8)


class TotpEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpActivateRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


# ── admin: users ─────────────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=12, max_length=_MAX_PASSWORD)
    role: Role = "viewer"


# ── admin: api tokens ────────────────────────────────────────────────────────


class CreateApiTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=list)


class ApiTokenOut(BaseModel):
    id: int
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


class ApiTokenCreated(ApiTokenOut):
    # The plaintext token — returned once, at creation, and never again.
    token: str


# ── cameras ──────────────────────────────────────────────────────────────────


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    source_url: str = Field(min_length=1, max_length=500)

    @field_validator("source_url")
    @classmethod
    def _require_rtsp(cls, value: str) -> str:
        # The source is handed to ffprobe and go2rtc verbatim. Restrict it to
        # rtsp(s):// so a value can't select another ffprobe protocol (file:,
        # http:, data:, …) or be parsed as an option — closing off local-file
        # reads / SSRF and keeping the "RTSP contract" honest.
        scheme = urlsplit(value).scheme.lower()
        if scheme not in ("rtsp", "rtsps"):
            raise ValueError("source_url must be an rtsp:// or rtsps:// URL")
        return value


class CameraOut(BaseModel):
    id: int
    name: str
    # source_url is deliberately omitted: RTSP URLs embed camera credentials and
    # the internal camera IP. It is admin-only input and is never echoed back —
    # clients watch only via the api-relayed stream, never the raw source.
    codec: str
    width: int
    height: int
    enabled: bool
    has_audio: bool = False
    online: bool | None = None
    last_checked: datetime | None = None


class AudioStatusOut(BaseModel):
    # Whether a standalone host microphone (the audio adapter) is available to listen
    # to independently of any camera.
    available: bool


# ── clips (recordings) ───────────────────────────────────────────────────────


class ClipCreate(BaseModel):
    # Timezone-aware only: naive input would later mix with the recorder's UTC-aware
    # segment times and raise. Clients send ISO-8601 with an offset (e.g. ...Z).
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, end: datetime, info: ValidationInfo) -> datetime:
        start = info.data.get("start")
        if start is not None and end <= start:
            raise ValueError("end must be after start")
        return end


class ClipOut(BaseModel):
    id: int
    camera_id: int
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime
    actual_end: datetime
    duration_seconds: float
    size_bytes: int
    codec: str
    created_at: datetime


class EventOut(BaseModel):
    """An insight event for the Tonight view — nudge events carry a clip_id once the
    worker has auto-promoted a clip (null until then)."""

    id: int
    ts: datetime
    camera_id: int
    type: str
    value: str
    previous_value: str | None
    confidence: float
    clip_id: int | None


class FusedSegmentOut(BaseModel):
    """One span of constant fused state on the Tonight timeline. ``is_open`` marks the
    still-ongoing segment. Awareness states only — sleep/wake, calm/distressed."""

    start: datetime
    end: datetime
    sleep: str  # "sleep" | "wake"
    arousal: str  # "calm" | "distressed"
    is_open: bool


class SleepSessionOut(BaseModel):
    """A consolidated sleep period; ``ended_at`` is null while still in progress."""

    started_at: datetime
    ended_at: datetime | None


class TonightTimelineOut(BaseModel):
    """The M3.3 Tonight timeline: fused-state segments + sleep sessions over a window
    (events come from ``GET /events`` and are overlaid client-side)."""

    start: datetime
    end: datetime
    segments: list[FusedSegmentOut]
    sessions: list[SleepSessionOut]


class TrendNight(BaseModel):
    """One night's sleep rollup (M4.1). Durations in seconds; the UI formats to hours.
    Awareness metrics only — never a medical or vital-sign readout."""

    night: datetime
    sessions: int
    total_sleep_s: float
    wakes: int
    longest_stretch_s: float


class TrendWeek(BaseModel):
    """A week's rollup for the week-over-week view (M4.1)."""

    week: datetime
    nights: int
    total_sleep_s: float
    avg_sleep_s: float
    wakes: int
    longest_stretch_s: float


class PulseOxMessage(BaseModel):
    """The pulse-ox node wire contract (M4.2), published to ``eeper/dev/{id}/pulseox``.
    ``quality`` is mandatory — ingestion discards low-confidence samples rather than
    storing misleading data. Insights-only: these feed trends + fusion features, never a
    vital-sign readout. ``extra='forbid'`` + a byte cap make a malformed message a
    logged drop, never a crash."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(gt=0)  # node event time (unix seconds)
    hr: float = Field(ge=0, le=350)  # heart rate, bpm
    spo2: float = Field(ge=0, le=100)  # blood-oxygen estimate, %
    perfusion: float = Field(ge=0, le=100)  # perfusion index, %
    quality: float = Field(ge=0.0, le=1.0)  # sample confidence (mandatory)


class PulseOxDeviceHealth(BaseModel):
    """Per-device pulse-ox ingest stats (M4.2) so the quality-gate discard rate is
    observable. Counts are for the current run."""

    device_id: int
    accepted: int
    discarded: int
    discard_rate: float


class PulseOxTrendPoint(BaseModel):
    """One hour's average heart rate (M4.2) — trend context, from quality-gated samples
    only. Not a live readout."""

    hour: datetime
    hr_avg: float
    samples: int


class PulseOxStatus(BaseModel):
    """The pulse-ox gate state (M4.2). ``enabled`` is the AND of the profile being on and
    an admin having acknowledged the current disclaimer — pulse-ox is inert otherwise."""

    profile_enabled: bool
    acknowledged: bool
    enabled: bool
    disclaimer_version: str


class PulseOxDisclaimer(BaseModel):
    version: str
    text: str
    accuracy_caveat: str
    safe_sleep_url: str


class PulseOxAcknowledge(BaseModel):
    """An admin confirming they read the current disclaimer. ``version`` must match the
    current one, so an acknowledgment can't silently apply to text the admin didn't see."""

    version: str = Field(min_length=1, max_length=16)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushSubscriptionIn(BaseModel):
    """The browser's ``PushSubscription.toJSON()`` shape."""

    endpoint: str = Field(min_length=1, max_length=1000)
    keys: PushKeys


class VapidKeyOut(BaseModel):
    public_key: str


class NotificationPreferencesOut(BaseModel):
    push_enabled: bool
    quiet_hours_enabled: bool
    quiet_hours_start: int
    quiet_hours_end: int
    timezone: str


class NotificationPreferencesIn(BaseModel):
    """Partial update — only the provided fields change. Minutes-of-day in [0, 1440)."""

    push_enabled: bool | None = None
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=1439)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=1439)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


# ── devices + the MQTT sensor contract (M3.1) ────────────────────────────────

DeviceKind = Literal["mmwave", "pir", "thermal", "other"]


class SensorMessage(BaseModel):
    """The MQTT sensor-node wire contract. A node publishes this JSON to
    ``eeper/dev/{id}/{metric}``. ``extra='forbid'`` rejects unknown fields; the
    ingestion service also caps the raw byte size before parsing, so a malformed or
    oversized message is dropped and logged — never crashing or slowing ingestion."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(gt=0)  # node event time (unix seconds)
    type: str = Field(min_length=1, max_length=32)  # metric: "movement" | "presence" | …
    value: float
    unit: str = Field(min_length=1, max_length=16)
    quality: float = Field(ge=0.0, le=1.0)


# ── thermal contract (M6.1, §4.5) ─────────────────────────────────────────────

# The MLX90640 is a 32×24 array → 768 cells, published row-major. Fixed by the contract.
THERMAL_COLS = 32
THERMAL_ROWS = 24
THERMAL_CELLS = THERMAL_ROWS * THERMAL_COLS
# Plausible surface-temperature envelope for the sensor; a value outside it (or NaN/inf)
# marks a malformed frame that must never be published as a good grid.
_THERMAL_T_MIN = -40.0
_THERMAL_T_MAX = 300.0


class ThermalGridMessage(BaseModel):
    """The full thermal grid wire contract (M6.1, §4.5), published to
    ``eeper/dev/{id}/thermal`` at 2–4 Hz. The grid exists for CHARACTERIZATION and debug;
    the fusion layer consumes only the derived :class:`ThermalFeaturesMessage`. Surface
    temperatures only — never a body-temperature readout (§2). ``quality`` is mandatory;
    a truncated grid or any non-finite / out-of-range temperature is rejected here, so a
    malformed frame can never validate (the publisher drops it rather than emitting it)."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(gt=0)  # node event time (unix seconds)
    grid: list[float] = Field(min_length=THERMAL_CELLS, max_length=THERMAL_CELLS)  # °C, row-major
    t_min: float
    t_max: float
    t_mean: float
    quality: float = Field(ge=0.0, le=1.0)

    @field_validator("grid")
    @classmethod
    def _finite_in_range(cls, grid: list[float]) -> list[float]:
        for t in grid:
            if not math.isfinite(t) or not (_THERMAL_T_MIN <= t <= _THERMAL_T_MAX):
                raise ValueError("grid contains a non-finite or out-of-range temperature")
        return grid


class ThermalFeaturesMessage(BaseModel):
    """The derived low-rate thermal features (M6.1, §4.5), published to
    ``eeper/dev/{id}/thermal_features``. This is the ONLY thermal signal the fusion layer
    consumes — presence + warm-region shape, never a temperature. ``warm_region_centroid``
    is ``[row, col]`` normalized to [0, 1] (or null when no warm region is present)."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(gt=0)
    presence: bool
    presence_confidence: float = Field(ge=0.0, le=1.0)
    warm_region_area: float = Field(ge=0.0, le=1.0)  # fraction of cells above the warm threshold
    warm_region_centroid: list[float] | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("warm_region_centroid")
    @classmethod
    def _centroid_in_unit_square(cls, c: list[float] | None) -> list[float] | None:
        if c is not None and not all(0.0 <= v <= 1.0 for v in c):
            raise ValueError("centroid components must be normalized to [0, 1]")
        return c


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    kind: DeviceKind = "other"


class DeviceOut(BaseModel):
    id: int
    name: str
    kind: str
    enabled: bool
    # Derived from last_seen_at vs the heartbeat window; None until the first reading.
    online: bool | None = None
    last_seen_at: datetime | None = None


class DevicePaired(DeviceOut):
    """Returned ONCE, at pairing — the node's MQTT identity (username + password) and
    its topic prefix. The password is never stored server-side or echoed again."""

    mqtt_username: str
    mqtt_password: str
    topic_prefix: str  # eeper/dev/{id}/
