"""SQLAlchemy models.

M0.2 needs only ``users``. A ``household_id`` is carried from day one so the
future hosted/multi-tenant deployment is a policy layer, not a migration
(Master Plan §12). ``role`` exists now but only ``admin`` is issued until M0.3
adds the viewer role and the full auth matrix.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
