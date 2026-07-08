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
        if ext.first() is None:
            return  # no TimescaleDB (unit env) — plain tables are sufficient
        for table in _HYPERTABLES:
            # if_not_exists => TRUE makes this a NOTICE-only no-op when already a
            # hypertable, so it is safe to run on every boot.
            await conn.exec_driver_sql(
                f"SELECT create_hypertable('{table}', 'ts', if_not_exists => TRUE)"
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
