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
