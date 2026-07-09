"""Async database engine and session wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from eeper.api.config import get_settings
from eeper.api.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


# Tables that must become TimescaleDB hypertables, partitioned on their ``ts``
# column (M2.2). Fixed literals — never user input — so the create_hypertable call
# below builds a safe SQL string.
_HYPERTABLES = ("state_history", "events")

# A lightweight forward migration (M2.4) for deployments whose `events` table predates
# the delivery-state columns: create_all only CREATEs missing tables, never ALTERs an
# existing one, so an upgraded DB would lack these columns and the notify trigger below
# (which references them) would fail to create and abort boot. ADD COLUMN IF NOT EXISTS
# is a no-op on a fresh table and idempotent on every boot. (Alembic supersedes this
# when the schema needs richer in-place evolution.)
_EVENT_COLUMN_MIGRATION = """
    ALTER TABLE events
        ADD COLUMN IF NOT EXISTS clip_status varchar(12) NOT NULL DEFAULT 'skip',
        ADD COLUMN IF NOT EXISTS nudge_status varchar(12) NOT NULL DEFAULT 'skip',
        ADD COLUMN IF NOT EXISTS broadcast_status varchar(12) NOT NULL DEFAULT 'skip',
        ADD COLUMN IF NOT EXISTS delivery_attempts integer NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS delivered_at timestamptz
"""

# The nudge-worker wake-up (M2.4). An AFTER INSERT trigger on `events` fires
# pg_notify only for a nudge-worthy row (a channel still "pending"). NOTIFY is
# TRANSACTIONAL — delivered on commit, never on rollback — so the wake-up can never
# race ahead of the committed row or fire for an aborted insert (the failure mode an
# MQTT publish has). The worker also reconciles by polling, so a dropped notify is a
# latency hit, not a lost nudge. Idempotent (CREATE OR REPLACE + DROP IF EXISTS), so
# every boot re-applies it safely.
_EVENT_NOTIFY_SQL = (
    """
    CREATE OR REPLACE FUNCTION eeper_notify_new_event() RETURNS trigger AS $$
    BEGIN
      PERFORM pg_notify('eeper_new_event', NEW.id::text);
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS eeper_events_notify ON events",
    """
    CREATE TRIGGER eeper_events_notify AFTER INSERT ON events
      FOR EACH ROW
      WHEN (NEW.clip_status = 'pending' OR NEW.nudge_status = 'pending'
            OR NEW.broadcast_status = 'pending')
      EXECUTE FUNCTION eeper_notify_new_event()
    """,
)


async def create_schema() -> None:
    """Create tables if they don't exist (M0.2; Alembic migrations come later)."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_schema_and_hypertables() -> None:
    """Create all tables, then convert the time-series tables to hypertables.

    Called by BOTH the api lifespan and the insight-engine startup — the insight
    engine WRITES ``state_history`` so it cannot assume the api ran first. A single
    transaction-scoped advisory lock serializes concurrent boots (the loser's
    ``create_all`` is a no-op via checkfirst and ``create_hypertable`` is idempotent
    via ``if_not_exists``), so the schema is race-free regardless of start order.

    On a database without the TimescaleDB extension (e.g. a plain-Postgres unit
    environment) the hypertable conversion is skipped and the plain tables stand in.
    """
    async with get_engine().begin() as conn:
        # hashtext() -> a stable advisory-lock key; released at transaction end.
        await conn.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('eeper_schema'))")
        await conn.run_sync(Base.metadata.create_all)
        ext = await conn.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if ext.first() is not None:
            for table in _HYPERTABLES:
                # if_not_exists => TRUE makes this a NOTICE-only no-op when already a
                # hypertable, so it is safe to run on every boot.
                await conn.exec_driver_sql(
                    f"SELECT create_hypertable('{table}', 'ts', if_not_exists => TRUE)"
                )
        # Add any missing delivery-state columns (upgrade path) BEFORE the trigger,
        # which references them; then the wake-up trigger itself — after any hypertable
        # conversion, on plain Postgres too. Idempotent, so every boot re-applies them.
        await conn.exec_driver_sql(_EVENT_COLUMN_MIGRATION)
        for stmt in _EVENT_NOTIFY_SQL:
            await conn.exec_driver_sql(stmt)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
