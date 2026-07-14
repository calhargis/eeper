"""M4.3 time-series retention, against a REAL TimescaleDB (testcontainer — retention
policies are a TimescaleDB feature):

* a retention policy is registered on each raw-telemetry hypertable (state_history,
  sensor_readings, pulseox_readings) with the configured drop-after, and NOT on the
  derived/history/trends tables (events, fused_states, sleep_sessions);
* running the policy job actually drops chunks older than the window while keeping recent
  data — the eviction half of the retention matrix for the time series.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import eeper.api.db as db
from eeper.api.config import get_settings
from eeper.api.models import Base

_TS_IMAGE = "timescale/timescaledb:latest-pg16@sha256:ba149561ad4ddff5940d6eb0a0df60aefd1355cee1a450928f271267038fc888"  # noqa: E501 (pinned digest)

_RETENTION_DAYS = 30


@pytest.fixture(scope="module")
def timescale_url() -> Iterator[str]:
    with PostgresContainer(_TS_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture
async def sm(
    timescale_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setenv("EEPER_DATABASE_URL", timescale_url)
    monkeypatch.setenv("EEPER_SECRET_KEY", "x" * 16)
    monkeypatch.setenv("EEPER_TIMESERIES_RETENTION_DAYS", str(_RETENTION_DAYS))
    get_settings.cache_clear()
    db._engine = None
    db._sessionmaker = None
    engine = create_async_engine(timescale_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        await conn.exec_driver_sql("DROP MATERIALIZED VIEW IF EXISTS trends_nightly CASCADE")
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    await db.create_schema_and_hypertables()
    yield db.get_sessionmaker()
    await db.get_engine().dispose()
    db._engine = None
    db._sessionmaker = None
    get_settings.cache_clear()


async def _retention_jobs() -> dict[str, str]:
    """hypertable_name -> drop_after, for every registered retention policy."""
    async with db.get_sessionmaker()() as s:
        rows = await s.execute(
            text(
                "SELECT hypertable_name, config->>'drop_after' AS drop_after "
                "FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'"
            )
        )
        return {r.hypertable_name: r.drop_after for r in rows}


async def test_retention_policy_registered_on_raw_telemetry_only(sm) -> None:  # type: ignore[no-untyped-def]
    jobs = await _retention_jobs()
    # Registered on the raw high-volume ingest streams…
    assert {"state_history", "sensor_readings", "pulseox_readings"} <= set(jobs)
    # …with the configured drop-after window.
    assert all("30 days" in v for v in jobs.values())
    # …and NOT on the derived/history/trends tables.
    assert "events" not in jobs
    assert "fused_states" not in jobs
    assert "sleep_sessions" not in jobs


async def test_running_the_job_drops_old_chunks_keeps_recent(sm) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    old = now - timedelta(days=_RETENTION_DAYS + 30)  # comfortably past the window
    fresh = now - timedelta(hours=1)

    async with sm() as s:
        for ts in (old, fresh):
            await s.execute(
                text(
                    "INSERT INTO pulseox_readings "
                    "(ts, household_id, device_id, hr, spo2, perfusion, quality) "
                    "VALUES (:ts, 'default', 1, 120, 98, 4, 0.9)"
                ),
                {"ts": ts},
            )
        await s.commit()

    # Run the retention job now instead of waiting for the scheduler.
    engine = db.get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        job = await conn.exec_driver_sql(
            "SELECT job_id FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' AND hypertable_name = 'pulseox_readings'"
        )
        job_id = job.scalar_one()
        await conn.exec_driver_sql(f"CALL run_job({int(job_id)})")

    async with sm() as s:
        remaining = (await s.execute(text("SELECT count(*) FROM pulseox_readings"))).scalar_one()
        oldest = (await s.execute(text("SELECT min(ts) FROM pulseox_readings"))).scalar_one()

    assert remaining == 1  # the old chunk was dropped, the fresh row kept
    assert oldest is not None and oldest > now - timedelta(days=_RETENTION_DAYS)
