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
_HYPERTABLES = (
    "state_history",
    "events",
    "sensor_readings",
    "fused_states",
    "pulseox_readings",
    "thermal_features",
)

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

    await _create_trends_objects()
    await _create_retention_policies()


# Retention (M4.3): an optional AGE bound on the raw high-volume telemetry hypertables via
# TimescaleDB retention policies that drop chunks older than `timeseries_retention_days`.
# Opt-in (0 disables). The derived/history tables (events, fused_states) and the trends
# source (sleep_sessions) are deliberately NOT here — they back the Tonight timeline and
# the long-term trends and are retained (sleep_sessions is compressed, not dropped).
_RETENTION_TABLES = ("state_history", "sensor_readings", "pulseox_readings")


async def _create_retention_policies() -> None:
    """Add/refresh TimescaleDB retention policies on the raw telemetry hypertables when
    `timeseries_retention_days` > 0 (TimescaleDB only; a no-op on plain Postgres or when
    disabled). Runs on an AUTOCOMMIT connection alongside the other policy DDL."""
    days = get_settings().timeseries_retention_days
    if days <= 0:
        return
    autocommit_engine = get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        ext = await conn.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if ext.first() is None:
            return
        await conn.exec_driver_sql("SELECT pg_advisory_lock(hashtext('eeper_retention'))")
        try:
            for table in _RETENTION_TABLES:
                # `days` is an int from validated config (never user input); if_not_exists
                # keeps a reboot from churning an already-registered policy.
                await conn.exec_driver_sql(
                    f"SELECT add_retention_policy('{table}', "
                    f"INTERVAL '{days} days', if_not_exists => TRUE)"
                )
        finally:
            await conn.exec_driver_sql("SELECT pg_advisory_unlock(hashtext('eeper_retention'))")


# Trends (M4.1): the sleep_sessions hypertable + a nightly continuous aggregate +
# compression. TimescaleDB-only, and idempotent (every clause is if-not-exists), so a
# reboot re-applies them safely. The continuous-aggregate DDL cannot run inside a
# transaction, so these run on a separate AUTOCOMMIT connection (unlike the schema above,
# which is one advisory-locked transaction). `sleep_sessions` is keyed on `started_at`,
# not `ts`, so it's created here rather than in the `_HYPERTABLES` loop.
_TRENDS_DDL = (
    "SELECT create_hypertable('sleep_sessions', 'started_at', "
    "if_not_exists => TRUE, migrate_data => TRUE)",
    # A night can hold multiple sessions (a nap + the night); the nightly rollup sums
    # them. Real-time by default, so a query includes not-yet-materialized recent rows.
    """
    CREATE MATERIALIZED VIEW IF NOT EXISTS trends_nightly
      WITH (timescaledb.continuous) AS
      SELECT time_bucket('1 day', started_at) AS night,
             household_id,
             count(*)               AS sessions,
             sum(total_sleep_s)     AS total_sleep_s,
             sum(wake_count)        AS wakes,
             max(longest_stretch_s) AS longest_stretch_s
      FROM sleep_sessions
      GROUP BY night, household_id
      WITH NO DATA
    """,
    "SELECT add_continuous_aggregate_policy('trends_nightly', "
    "start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour', "
    "schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE)",
    "ALTER TABLE sleep_sessions SET (timescaledb.compress, "
    "timescaledb.compress_segmentby = 'household_id', "
    "timescaledb.compress_orderby = 'started_at DESC, id')",
    "SELECT add_compression_policy('sleep_sessions', INTERVAL '7 days', if_not_exists => TRUE)",
    # Materialize the recent window on boot so Trends has data immediately (the hourly
    # policy keeps it current after; real-time aggregation covers the last hour). A no-op
    # on a fresh install. Kept last so a failure here can't block the DDL above.
    "CALL refresh_continuous_aggregate('trends_nightly', now() - INTERVAL '30 days', NULL)",
)


async def _create_trends_objects() -> None:
    """Create the Trends hypertable + continuous aggregate + compression (TimescaleDB
    only; a no-op on plain Postgres). Runs on an AUTOCOMMIT connection."""
    autocommit_engine = get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        ext = await conn.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        if ext.first() is None:
            return
        # Serialize concurrent boots (advisory lock, session-level in autocommit).
        await conn.exec_driver_sql("SELECT pg_advisory_lock(hashtext('eeper_trends'))")
        try:
            for stmt in _TRENDS_DDL:
                await conn.exec_driver_sql(stmt)
        finally:
            await conn.exec_driver_sql("SELECT pg_advisory_unlock(hashtext('eeper_trends'))")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
