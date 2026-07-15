"""SQLAlchemy models.

A ``household_id`` is carried from day one so the future hosted/multi-tenant
deployment is a policy layer, not a migration (Master Plan §12).

Note: schema is created with ``create_all`` (M0.x); there is no data to migrate
yet. Alembic migrations arrive when the schema needs to evolve in place.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="admin")

    # TOTP 2FA (optional). Secret is set at enrollment; enabled after activation.
    totp_secret: Mapped[str | None] = mapped_column(String(64), default=None)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Brute-force lockout state.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Camera(Base):
    """A registered camera. ``source_url`` is validated against the RTSP contract
    (H.264, <=1080p) at registration; the go2rtc stream is named ``cam{id}``."""

    __tablename__ = "cameras"
    # One registration per source within a household — a duplicate would spin up a
    # second go2rtc stream + health probe for the same physical camera.
    __table_args__ = (UniqueConstraint("household_id", "source_url", name="uq_camera_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    name: Mapped[str] = mapped_column(String(150))
    source_url: Mapped[str] = mapped_column(String(500))
    codec: Mapped[str] = mapped_column(String(20))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    # Whether the source carries audio — gates the Opus listen-in gateway source.
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Clip(Base):
    """A promoted recording clip: an H.264 MP4 cut from the ring-buffer segments
    and kept under ``/media/clips`` so it survives segment eviction. Stores both
    the requested window and the probed-actual window (keyframe-aligned copy is
    ±1 GOP, so they can differ slightly)."""

    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(500))
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    codec: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StateHistory(Base):
    """Time series of derived insight states (a TimescaleDB hypertable keyed on
    ``ts``). M2.2 writes ``movement_level`` (low/medium/high) from the camera
    motion score; later milestones add sleep/wake and calm/distressed rows.

    This is an insight/awareness signal, never a medical or vital-sign readout.

    The PK is composite ``(ts, id)``: a hypertable requires its partitioning column
    (``ts``) in every unique index, so a lone surrogate PK is rejected by
    TimescaleDB. ``ts`` is set explicitly at score time by the writer (no
    server_default) so the row's timestamp is the event time, not the insert time.
    """

    __tablename__ = "state_history"
    __table_args__ = (Index("ix_state_history_cam_ts", "camera_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    # Logical camera reference; no FK — a hypertable row is written by the insight
    # engine outside the ORM relationship graph, and FK-to/from a hypertable adds
    # friction for no benefit here.
    camera_id: Mapped[int] = mapped_column(BigInteger, index=True)
    state_type: Mapped[str] = mapped_column(String(32))  # e.g. "movement_level"
    value: Mapped[str] = mapped_column(String(16))  # e.g. "low" | "medium" | "high"
    confidence: Mapped[float] = mapped_column(Float)  # 0..1
    # Sorted CSV of the extractor names that fed this state, e.g. "motion".
    contributing_inputs: Mapped[str] = mapped_column(String(255), default="")


class Event(Base):
    """A discrete insight event (a TimescaleDB hypertable keyed on ``ts``). M2.2
    emits ``movement_level_change`` when the movement level transitions; later
    milestones add cry/other events, and ``clip_id`` links an auto-promoted clip.

    Awareness events only — the vocabulary is deliberately non-clinical. Composite
    ``(ts, id)`` PK for the same hypertable reason as :class:`StateHistory`."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_cam_ts", "camera_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    camera_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(48))  # e.g. "movement_level_change"
    value: Mapped[str] = mapped_column(String(16))  # the new level
    previous_value: Mapped[str | None] = mapped_column(String(16), default=None)
    confidence: Mapped[float] = mapped_column(Float)
    # Nullable link to a promoted clip; NULL until the M2.4 nudge worker auto-promotes
    # one. No FK for the same hypertable-friction reason.
    clip_id: Mapped[int | None] = mapped_column(BigInteger, default=None)

    # Delivery state (M2.4). The events table is a DB-as-queue: the insight engine
    # writes a nudge-worthy event with each channel "pending"; the api-side nudge
    # worker (LISTEN/NOTIFY + reconciliation poll) does the side effects and marks
    # them, so a crash mid-delivery resumes losslessly. Non-nudge events (movement,
    # cleared edges) default to "skip" and the worker never touches them. Delivery
    # POLICY (quiet hours, per-user prefs, rate-limit) lives in the worker, not here.
    # clip_status: skip|pending|promoted|failed; nudge_status (push):
    # skip|pending|sent|suppressed|failed; broadcast_status: skip|pending|sent.
    clip_status: Mapped[str] = mapped_column(String(12), default="skip")
    nudge_status: Mapped[str] = mapped_column(String(12), default="skip")
    broadcast_status: Mapped[str] = mapped_column(String(12), default="skip")
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class RefreshToken(Base):
    """A rotating refresh token. Stored hashed; grouped into a family per login
    session so a detected reuse can revoke the whole family."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiToken(Base):
    """A scoped, long-lived token for integrations (e.g. Home Assistant).
    Stored hashed; presented as ``Authorization: Bearer <token>``."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PushSubscription(Base):
    """A browser Web Push subscription (M2.4): one row per (user, endpoint). The
    endpoint URL + its p256dh/auth keys are what pywebpush encrypts a nudge to; a
    subscription is removed when the browser unsubscribes or the push service reports
    it gone (HTTP 404/410)."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", name="uq_push_user_endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000))
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationPreferences(Base):
    """Per-user nudge policy (M2.4): a master push toggle plus optional quiet hours.
    Quiet hours are minutes-of-day in [0, 1440) in the user's ``timezone``; the worker
    handles the wrap-around case (start > end, e.g. 22:00 -> 07:00). Read by the nudge
    worker at delivery time — the one place delivery policy lives."""

    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[int] = mapped_column(Integer, default=0)  # minutes of day
    quiet_hours_end: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Device(Base):
    """A registered sensor node (mmWave/PIR/…) that publishes readings over the
    hardened MQTT bus (M3.1). Pairing mints a per-device MQTT credential and a
    dynamic-security ACL scoped to ``eeper/dev/{id}/#`` — only the credential's
    username is kept here (the password is returned once at pairing and never stored).
    An input node, never a medical/vital-sign device."""

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("household_id", "name", name="uq_device_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(32))  # "mmwave" | "pir" | ...
    # The dynsec client username the node authenticates with (derived from the id at
    # pairing, e.g. "dev-7"); its topic subtree is eeper/dev/{id}/#.
    mqtt_username: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Last time a valid reading arrived — drives the online/offline health signal.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SensorReading(Base):
    """Time series of sensor-node readings (a TimescaleDB hypertable keyed on ``ts``),
    written by the MQTT ingestion service (M3.1). Columns follow the Master Plan schema
    ``(device_id, ts, metric, value, quality)``. ``ts`` is the node's own event time.

    This is an insight/awareness signal, never a medical or vital-sign readout.

    Composite PK ``(ts, id)`` for the same reason as ``state_history``: a hypertable
    needs its partitioning column in every unique index. ``device_id`` is a logical
    reference (no FK to/from a hypertable)."""

    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_sensor_readings_device_ts", "device_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    device_id: Mapped[int] = mapped_column(BigInteger, index=True)
    metric: Mapped[str] = mapped_column(String(32))  # e.g. "movement" | "presence"
    value: Mapped[float] = mapped_column(Float)
    quality: Mapped[float] = mapped_column(Float)  # 0..1


class FusedState(Base):
    """Fused sleep/wake + calm/distressed state transitions (M3.3), a TimescaleDB
    hypertable keyed on ``ts``. One row marks the moment a household's fused state
    *changed*; the state at any instant is the last row at or before it (carry-forward).
    The fusion worker derives these from every extractor's signals (camera motion,
    mmWave/PIR movement + presence, sound, cry). This transition log is the durable
    source of truth — sleep sessions are a query over it, and a worker restart re-derives
    the current state from the persisted signals, so nothing is lost.

    Awareness states only — sleep/wake and calm/distressed — never a medical, diagnostic,
    or vital-sign readout. Composite ``(ts, id)`` PK for the hypertable reason as the
    other time-series tables; ``household_id`` scopes it (no per-camera column — fusion
    is a whole-nursery signal)."""

    __tablename__ = "fused_states"
    __table_args__ = (Index("ix_fused_states_hh_ts", "household_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    sleep: Mapped[str] = mapped_column(String(8))  # "sleep" | "wake"
    arousal: Mapped[str] = mapped_column(String(12))  # "calm" | "distressed"
    activity: Mapped[float] = mapped_column(Float)  # 0..1 smoothed activity behind it
    confidence: Mapped[float] = mapped_column(Float)  # 0..1
    # Sorted CSV of the extractors that corroborated this state, e.g. "camera,sensor".
    contributing_inputs: Mapped[str] = mapped_column(String(255), default="")


class SleepSessionRecord(Base):
    """Materialized consolidated sleep sessions (M4.1) — a TimescaleDB hypertable keyed
    on ``started_at`` that is the source for the Trends continuous aggregates. The fusion
    worker writes a row as each session *closes* (the still-open session is derived on
    read from ``fused_states``, never stored), with per-session metrics computed from the
    fused-state timeline. Idempotent: ``(household_id, started_at)`` is unique, so
    re-materializing the same session is a no-op.

    Awareness signal only — sleep durations and wake counts, never a medical or
    vital-sign readout. Composite ``(started_at, id)`` PK for the hypertable reason as
    the other time-series tables (the partition column is in every unique index)."""

    __tablename__ = "sleep_sessions"
    __table_args__ = (
        UniqueConstraint("household_id", "started_at", name="uq_sleep_sessions_hh_start"),
        Index("ix_sleep_sessions_hh_start", "household_id", "started_at"),
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_sleep_s: Mapped[float] = mapped_column(Float)  # time actually asleep in the session
    wake_count: Mapped[int] = mapped_column(Integer)  # intra-session awakenings
    longest_stretch_s: Mapped[float] = mapped_column(Float)  # longest unbroken sleep span
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PulseOxReading(Base):
    """Quality-gated pulse-ox samples (M4.2), a TimescaleDB hypertable keyed on ``ts``.
    ONLY samples that cleared the quality gate are stored — low-confidence readings are
    discarded at ingestion, never persisted. Insights-only (heart-rate / blood-oxygen /
    perfusion as trend + fusion features), never a vital-sign readout or alarm. Composite
    ``(ts, id)`` PK for the hypertable reason; ``device_id`` is a logical reference."""

    __tablename__ = "pulseox_readings"
    __table_args__ = (Index("ix_pulseox_device_ts", "device_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    device_id: Mapped[int] = mapped_column(BigInteger, index=True)
    hr: Mapped[float] = mapped_column(Float)
    spo2: Mapped[float] = mapped_column(Float)
    perfusion: Mapped[float] = mapped_column(Float)
    quality: Mapped[float] = mapped_column(Float)  # >= the gate threshold (accepted only)


class ThermalFeaturesReading(Base):
    """Derived thermal features (M6.1, §4.5), a TimescaleDB hypertable keyed on ``ts``.
    Only the low-rate DERIVED features are stored — presence + warm-region shape, the sole
    thermal signal the fusion layer consumes (M6.3). The raw 32×24 grid is never persisted
    here (it is characterization-time only). Surface features only; nothing is a
    body-temperature readout (§2). Composite ``(ts, id)`` PK for the hypertable reason;
    ``device_id`` is a logical reference. Centroid is null when no warm region is present."""

    __tablename__ = "thermal_features"
    __table_args__ = (Index("ix_thermal_device_ts", "device_id", "ts"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    device_id: Mapped[int] = mapped_column(BigInteger, index=True)
    presence: Mapped[bool] = mapped_column(Boolean)
    presence_confidence: Mapped[float] = mapped_column(Float)
    warm_region_area: Mapped[float] = mapped_column(Float)
    centroid_row: Mapped[float | None] = mapped_column(Float, nullable=True)
    centroid_col: Mapped[float | None] = mapped_column(Float, nullable=True)


class PulseOxConsent(Base):
    """An admin's acknowledgment of the pulse-ox disclaimer for a household (M4.2).

    Pulse-oximetry stays fully inert until BOTH the `pulseox` profile is enabled AND a row
    here exists at the *current* disclaimer version. Bumping the disclaimer text
    (``DISCLAIMER_VERSION``) invalidates an older acknowledgment, so an admin must read and
    re-acknowledge. This is an insights-only opt-in — never a medical consent."""

    __tablename__ = "pulseox_consent"

    household_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    disclaimer_version: Mapped[str] = mapped_column(String(16))
    acknowledged_by: Mapped[int] = mapped_column(BigInteger)  # the admin user's id
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
