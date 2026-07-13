"""Trends API tests (M4.1 slice 2) against a real TimescaleDB (the endpoints read the
`trends_nightly` continuous aggregate): nightly + weekly rollups, CSV export matching the
API data, and viewer-role denial of export.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import eeper.api.db as db
from eeper.api.clock import get_now
from eeper.api.config import Settings, get_settings
from eeper.api.db import get_session
from eeper.api.main import app
from eeper.api.models import Base

_TS_IMAGE = "timescale/timescaledb:latest-pg16@sha256:ba149561ad4ddff5940d6eb0a0df60aefd1355cee1a450928f271267038fc888"  # noqa: E501
_BASE_URL = "https://testserver"
_PW = "correct horse battery staple"


@pytest.fixture(scope="module")
def timescale_url() -> Iterator[str]:
    with PostgresContainer(_TS_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture
async def ts_api(
    timescale_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SimpleNamespace]:
    monkeypatch.setenv("EEPER_DATABASE_URL", timescale_url)
    monkeypatch.setenv("EEPER_SECRET_KEY", "x" * 16)
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
    sm = db.get_sessionmaker()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            yield session

    clock = {"now": datetime.now(UTC)}
    settings = Settings(database_url=timescale_url, secret_key="x" * 16)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_now] = lambda: clock["now"]
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)

    def fresh() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url=_BASE_URL)

    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        yield SimpleNamespace(client=client, fresh=fresh, sm=sm, clock=clock)

    app.dependency_overrides.clear()
    await db.get_engine().dispose()
    db._engine = None
    db._sessionmaker = None
    get_settings.cache_clear()


async def _first_boot(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/system/first-boot", json={"username": "admin", "password": _PW})
    assert r.status_code == 201, r.text


async def _seed(sm: async_sessionmaker[AsyncSession], start: datetime, days: int) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO sleep_sessions (started_at, household_id, ended_at, "
                "total_sleep_s, wake_count, longest_stretch_s) "
                "SELECT d, 'default', d + interval '8 hours', "
                "  28800 + (extract(epoch from d)::bigint % 3600), "
                "  (extract(epoch from d)::bigint % 4), "
                "  14400 + (extract(epoch from d)::bigint % 1800) "
                "FROM generate_series(:start ::timestamptz, "
                "  :start ::timestamptz + make_interval(days => :days), interval '1 day') d"
            ),
            {"start": start, "days": days},
        )
        await s.commit()
    engine = db.get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(
            "CALL refresh_continuous_aggregate('trends_nightly', NULL, NULL)"
        )


async def test_nightly_and_weekly_rollups(ts_api: SimpleNamespace) -> None:
    await _first_boot(ts_api.client)
    now = ts_api.clock["now"]
    await _seed(ts_api.sm, now - timedelta(days=27), days=27)  # ~4 weeks

    nightly = (await ts_api.client.get("/api/v1/trends/nightly")).json()
    assert len(nightly) >= 27
    assert all(
        {"night", "total_sleep_s", "sessions", "wakes", "longest_stretch_s"} <= n.keys()
        for n in nightly
    )
    assert all(n["total_sleep_s"] > 0 for n in nightly)

    weekly = (await ts_api.client.get("/api/v1/trends/weekly")).json()
    assert len(weekly) >= 4
    # Each week's total is the sum of its nights; avg is between the smallest and largest.
    for w in weekly:
        assert w["total_sleep_s"] == pytest.approx(w["avg_sleep_s"] * w["nights"], rel=1e-6)


async def test_csv_export_matches_api(ts_api: SimpleNamespace) -> None:
    await _first_boot(ts_api.client)
    now = ts_api.clock["now"]
    await _seed(ts_api.sm, now - timedelta(days=40), days=40)

    params = {"since": (now - timedelta(days=90)).isoformat()}
    nightly = (await ts_api.client.get("/api/v1/trends/nightly", params=params)).json()
    resp = await ts_api.client.get("/api/v1/trends/export.csv", params=params)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["night", "sessions", "total_sleep_hours", "wakes", "longest_stretch_hours"]
    assert len(rows) - 1 == len(nightly)  # one data row per night
    # Every CSV row matches the API's numbers (hours rounded).
    by_night = {n["night"][:10]: n for n in nightly}
    for night, sessions, sleep_h, wakes, longest_h in rows[1:]:
        api = by_night[night]
        assert int(sessions) == api["sessions"]
        assert int(wakes) == api["wakes"]
        assert float(sleep_h) == pytest.approx(round(api["total_sleep_s"] / 3600, 2))
        assert float(longest_h) == pytest.approx(round(api["longest_stretch_s"] / 3600, 2))


async def test_viewer_cannot_export_but_can_view(ts_api: SimpleNamespace) -> None:
    await _first_boot(ts_api.client)
    # Admin creates a viewer ("grandparent mode").
    created = await ts_api.client.post(
        "/api/v1/users", json={"username": "grandparent", "password": _PW, "role": "viewer"}
    )
    assert created.status_code == 201, created.text

    async with ts_api.fresh() as viewer:
        login = await viewer.post(
            "/api/v1/auth/login", json={"username": "grandparent", "password": _PW}
        )
        assert login.status_code == 200
        assert (await viewer.get("/api/v1/trends/nightly")).status_code == 200  # can view
        assert (await viewer.get("/api/v1/trends/export.csv")).status_code == 403  # cannot export


async def test_export_requires_auth(ts_api: SimpleNamespace) -> None:
    async with ts_api.fresh() as anon:
        assert (await anon.get("/api/v1/trends/export.csv")).status_code == 401
        assert (await anon.get("/api/v1/trends/nightly")).status_code == 401
