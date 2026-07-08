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
