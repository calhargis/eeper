"""Live-monitor lite (EEPER_LITE) — the low-RAM build that serves only login + camera +
room audio. These tests pin the two behaviours that make it safe:

* schema creation SKIPS the TimescaleDB hypertables/aggregate in lite (against a real
  TimescaleDB, so "full" genuinely creates them and "lite" genuinely doesn't);
* the app OMITS the heavy routers in lite, so it never advertises an endpoint whose table
  it didn't create, while auth + camera + audio stay registered.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

import eeper.api.db as db
from eeper.api.config import get_settings
from eeper.api.models import Base

# Same pinned TimescaleDB image the Trends tests use — the hypertable/aggregate features
# don't exist on plain Postgres, so we need the real extension to prove lite skips them.
_TS_IMAGE = "timescale/timescaledb:latest-pg16@sha256:ba149561ad4ddff5940d6eb0a0df60aefd1355cee1a450928f271267038fc888"  # noqa: E501


@pytest.fixture(scope="module")
def timescale_url() -> Iterator[str]:
    with PostgresContainer(_TS_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture
async def fresh_db(
    timescale_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A clean database + a reset db module, so each test builds the schema from scratch."""
    monkeypatch.setenv("EEPER_DATABASE_URL", timescale_url)
    monkeypatch.setenv("EEPER_SECRET_KEY", "x" * 16)
    get_settings.cache_clear()
    db._engine = None
    db._sessionmaker = None
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(timescale_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        await conn.exec_driver_sql("DROP MATERIALIZED VIEW IF EXISTS trends_nightly CASCADE")
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    yield db.get_sessionmaker()
    await db.get_engine().dispose()
    db._engine = None
    db._sessionmaker = None
    get_settings.cache_clear()


async def _hypertable_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as s:
        result = await s.execute(text("SELECT count(*) FROM timescaledb_information.hypertables"))
        return int(result.scalar_one())


async def _table_exists(sm: async_sessionmaker[AsyncSession], name: str) -> bool:
    async with sm() as s:
        return bool(
            (await s.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar_one() is not None
        )


async def test_full_creates_hypertables(
    fresh_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EEPER_LITE", raising=False)
    get_settings.cache_clear()
    await db.create_schema_and_hypertables()
    assert await _hypertable_count(fresh_db) > 0, "full mode should build TimescaleDB hypertables"
    async with fresh_db() as s:  # the continuous aggregate is a full-mode-only object
        assert (await s.execute(text("SELECT to_regclass('trends_nightly')"))).scalar_one()


async def test_lite_skips_hypertables_keeps_plain_tables(
    fresh_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EEPER_LITE", "true")
    get_settings.cache_clear()
    assert get_settings().lite is True

    await db.create_schema_and_hypertables()

    # No hypertables and no trends aggregate in lite...
    assert await _hypertable_count(fresh_db) == 0, "lite must not create hypertables"
    async with fresh_db() as s:
        assert (await s.execute(text("SELECT to_regclass('trends_nightly')"))).scalar_one() is None
    # ...but the plain relations still exist (empty), so nothing kept hits a missing table.
    assert await _table_exists(fresh_db, "users")
    assert await _table_exists(fresh_db, "cameras")
    assert await _table_exists(fresh_db, "sensor_readings")  # plain table, just never a hypertable
    assert await _table_exists(fresh_db, "events")  # kept for its notify trigger


def test_lite_omits_heavy_routers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Router registration is decided at import time from EEPER_LITE, so reload the app
    module under each mode and compare the mounted paths. The dropped routers must be absent
    (they'd 500 on a missing table); auth + camera + audio must remain."""
    monkeypatch.setenv("EEPER_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
    monkeypatch.setenv("EEPER_SECRET_KEY", "x" * 16)
    import eeper.api.main as main

    def paths_for(lite: bool) -> set[str]:
        if lite:
            monkeypatch.setenv("EEPER_LITE", "true")
        else:
            monkeypatch.delenv("EEPER_LITE", raising=False)
        get_settings.cache_clear()
        importlib.reload(main)
        return set(main.app.openapi()["paths"])  # the flattened, mounted endpoint paths

    try:
        full, lite = paths_for(False), paths_for(True)
    finally:
        monkeypatch.delenv("EEPER_LITE", raising=False)
        get_settings.cache_clear()
        importlib.reload(main)  # restore the shared module for any later test

    kept = "/api/v1/auth/login"
    dropped_prefixes = ("/api/v1/trends", "/api/v1/fusion", "/api/v1/thermal", "/api/v1/pulseox")
    assert kept in full and kept in lite, "auth stays registered in both modes"
    assert any(p.startswith("/api/v1/trends") for p in full), "full advertises trends"
    assert "/api/v1/cameras" in {p.rstrip("/") for p in lite} or any(
        p.startswith("/api/v1/cameras") for p in lite
    ), "lite keeps the camera endpoints"
    for prefix in dropped_prefixes:
        assert not any(p.startswith(prefix) for p in lite), f"lite must omit {prefix}"
