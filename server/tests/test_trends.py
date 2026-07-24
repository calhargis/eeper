"""M4.1 slice 1 — the Trends data foundation, against a REAL TimescaleDB (testcontainer,
since the aggregate/compression features don't exist on plain Postgres):

* materialization computes per-session metrics from the fused-state timeline;
* the nightly continuous aggregate matches an independent GROUP BY exactly;
* compression of > 7-day chunks preserves query results;
* a Trends rollup over a seeded year returns well under the 200 ms gate.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import eeper.api.db as db
from eeper.api.config import get_settings
from eeper.api.fusion_read import materialize_closed_sessions
from eeper.api.models import Base, FusedState, SleepSessionRecord

_TS_IMAGE = "timescale/timescaledb:latest-pg16@sha256:ba149561ad4ddff5940d6eb0a0df60aefd1355cee1a450928f271267038fc888"  # noqa: E501 (pinned digest)
_PERF_BUDGET_S = 0.2  # the M4.1 < 200 ms query gate


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
    get_settings.cache_clear()
    db._engine = None
    db._sessionmaker = None
    # Fresh schema per test (drop the continuous aggregate first — it depends on the table).
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


async def _autocommit(sql: str) -> None:
    """Run a statement that can't be in a transaction (refresh / compress)."""
    engine = db.get_engine().execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(sql)


async def _refresh() -> None:
    await _autocommit("CALL refresh_continuous_aggregate('trends_nightly', NULL, NULL)")


async def _seed_sessions(sm: async_sessionmaker[AsyncSession], start: datetime, days: int) -> None:
    """Seed one deterministic session per night, directly (the materialization path is
    covered separately)."""
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


async def test_materialize_computes_session_metrics(sm: async_sessionmaker[AsyncSession]) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    base = now - timedelta(days=1)

    def fs(offset: timedelta, sleep: str) -> FusedState:
        return FusedState(
            ts=base + offset,
            household_id="default",
            sleep=sleep,
            arousal="calm",
            activity=0.1 if sleep == "sleep" else 0.6,
            confidence=0.7,
            contributing_inputs="sensor",
        )

    async with sm() as s:
        s.add_all(
            [
                fs(timedelta(0), "wake"),
                fs(timedelta(minutes=10), "sleep"),
                fs(timedelta(hours=3), "wake"),  # a brief intra-session awakening
                fs(timedelta(hours=3, minutes=5), "sleep"),
                fs(timedelta(hours=8), "wake"),  # a long wake closes the session
            ]
        )
        await s.commit()
    async with sm() as s:
        written = await materialize_closed_sessions(s, "default", now - timedelta(days=2), now)
        await s.commit()
        assert written == 1
        # Re-materializing the same window is idempotent (ON CONFLICT DO NOTHING).
        assert await materialize_closed_sessions(s, "default", now - timedelta(days=2), now) == 0

    async with sm() as s:
        rec = (await s.execute(select(SleepSessionRecord))).scalar_one()
    assert rec.total_sleep_s == pytest.approx(7.75 * 3600, abs=60)  # ~7h45m asleep
    assert rec.wake_count == 1
    assert rec.longest_stretch_s == pytest.approx(4.92 * 3600, abs=60)  # 4h55m longest run


async def test_materialize_dedups_across_sliding_window(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """The live worker re-materializes over ``now - lookback .. now`` every ~30 s cycle, so
    the window origin slides with the wall clock. A single closed session must yield exactly
    ONE row no matter how many cycles run over it: ``started_at`` is anchored to the absolute
    epoch grid, so ON CONFLICT dedups. Before the anchor, each cycle snapped the session to a
    drifting ``started_at`` and inserted a fresh near-duplicate — the Trends bug this guards."""
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    base = now - timedelta(days=1)

    def fs(offset: timedelta, sleep: str) -> FusedState:
        return FusedState(
            ts=base + offset,
            household_id="default",
            sleep=sleep,
            arousal="calm",
            activity=0.1 if sleep == "sleep" else 0.6,
            confidence=0.7,
            contributing_inputs="sensor",
        )

    async with sm() as s:
        s.add_all(
            [
                fs(timedelta(0), "wake"),
                fs(timedelta(minutes=10), "sleep"),
                fs(timedelta(hours=8), "wake"),  # a long wake closes the one session
            ]
        )
        await s.commit()

    inserted = 0
    async with sm() as s:
        # Ten consecutive cycles, each 31 s later than the last, so the 26 h lookback window
        # never lands on the same sub-epoch offset twice — mirroring real cycles, which are
        # never exactly 30 s apart and never start on an epoch boundary.
        for k in range(10):
            cyc_now = now + timedelta(seconds=31 * k)
            inserted += await materialize_closed_sessions(
                s, "default", cyc_now - timedelta(hours=26), cyc_now
            )
            await s.commit()

    async with sm() as s:
        starts = (await s.execute(select(SleepSessionRecord.started_at))).scalars().all()
    assert inserted == 1, f"expected a single insert across the sliding cycles, got {inserted}"
    assert len(starts) == 1, f"expected 1 sleep_sessions row, got {len(starts)} (dedup failed)"


async def test_materialize_dedups_session_ongoing_at_window_start(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    """A session already underway at the lookback window's LEFT edge has its ``started_at``
    clamped to that edge, which slides every cycle — so the grid anchor alone can't keep it
    stable (only ``ended_at``, the in-window waking transition, is fixed). Dedup keys on
    ``ended_at``, so this boundary-spanning session still yields exactly ONE row. Guards the
    residual the anchor doesn't cover (the real Pi data was full of these long bouts)."""
    onset = datetime(2026, 7, 12, 22, 0, tzinfo=UTC)  # falls asleep...
    wake = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)  # ...wakes 10 h later
    async with sm() as s:
        s.add_all(
            [
                FusedState(
                    ts=onset,
                    household_id="default",
                    sleep="sleep",
                    arousal="calm",
                    activity=0.1,
                    confidence=0.7,
                    contributing_inputs="sensor",
                ),
                FusedState(
                    ts=wake,
                    household_id="default",
                    sleep="wake",
                    arousal="calm",
                    activity=0.6,
                    confidence=0.7,
                    contributing_inputs="sensor",
                ),
            ]
        )
        await s.commit()

    inserted = 0
    async with sm() as s:
        # Cycles run LATE — the 26 h window's start has already swept past `onset`, so every
        # cycle sees the session as ongoing-at-the-edge (start_epoch 0 → clamped, drifting
        # started_at) while `wake` still sits inside the window.
        for k in range(10):
            cyc_now = datetime(2026, 7, 14, 4, 0, tzinfo=UTC) + timedelta(seconds=31 * k)
            inserted += await materialize_closed_sessions(
                s, "default", cyc_now - timedelta(hours=26), cyc_now
            )
            await s.commit()

    async with sm() as s:
        rows = (await s.execute(select(SleepSessionRecord.ended_at))).scalars().all()
    assert inserted == 1, f"expected a single insert for the boundary session, got {inserted}"
    assert len(rows) == 1, f"expected 1 row, got {len(rows)} (ended_at dedup failed)"


async def test_continuous_aggregate_matches_independent(
    sm: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sessions(sm, datetime(2026, 6, 1, tzinfo=UTC), days=30)
    await _refresh()
    async with sm() as s:
        ca = (
            await s.execute(
                text(
                    "SELECT night, household_id, sessions, total_sleep_s, wakes, longest_stretch_s "
                    "FROM trends_nightly ORDER BY night"
                )
            )
        ).all()
        raw = (
            await s.execute(
                text(
                    "SELECT time_bucket('1 day', started_at), household_id, count(*), "
                    "sum(total_sleep_s), sum(wake_count), max(longest_stretch_s) "
                    "FROM sleep_sessions GROUP BY 1, 2 ORDER BY 1"
                )
            )
        ).all()
    assert len(ca) == 31
    assert [tuple(r) for r in ca] == [tuple(r) for r in raw]  # exact match, every column


async def test_compression_preserves_query_results(sm: async_sessionmaker[AsyncSession]) -> None:
    await _seed_sessions(sm, datetime.now(UTC) - timedelta(days=40), days=39)
    async with sm() as s:
        control = (
            await s.execute(
                text("SELECT count(*), sum(total_sleep_s), sum(wake_count) FROM sleep_sessions")
            )
        ).one()

    await _autocommit(
        "SELECT compress_chunk(c) FROM "
        "show_chunks('sleep_sessions', older_than => INTERVAL '7 days') c"
    )

    async with sm() as s:
        compressed = (
            await s.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'sleep_sessions' AND is_compressed"
                )
            )
        ).scalar_one()
        after = (
            await s.execute(
                text("SELECT count(*), sum(total_sleep_s), sum(wake_count) FROM sleep_sessions")
            )
        ).one()
    assert compressed > 0, "no chunks were compressed"
    assert tuple(after) == tuple(control)  # queries over compressed data are identical


async def test_trends_query_under_200ms(sm: async_sessionmaker[AsyncSession]) -> None:
    await _seed_sessions(sm, datetime.now(UTC) - timedelta(days=365), days=365)
    await _refresh()
    async with sm() as s:
        query = text(
            "SELECT time_bucket('7 days', night) week, sum(total_sleep_s), sum(sessions), "
            "max(longest_stretch_s) FROM trends_nightly WHERE household_id = 'default' "
            "GROUP BY week ORDER BY week DESC"
        )
        await s.execute(query)  # warm caches/plan
        start = time.perf_counter()
        rows = (await s.execute(query)).all()
        elapsed = time.perf_counter() - start
    assert len(rows) >= 52
    assert elapsed < _PERF_BUDGET_S, f"trends query took {elapsed * 1000:.0f} ms"
