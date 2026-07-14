"""Live fusion-worker tests (M3.3 slice 2) against a real Postgres: the worker turns
persisted extractor signals into fused_states transitions, and — the crash-recovery
criterion — a sleep session survives a worker restart because the transition log is the
only state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from eeper.api.config import Settings
from eeper.api.fusion_read import sleep_sessions
from eeper.api.fusion_signals import load_epoch_features
from eeper.api.fusion_worker import FusionWorker
from eeper.api.models import Base, FusedState, PulseOxReading, SensorReading, StateHistory
from eeper.fusion.model import EPOCH_SECONDS

WARMUP_MIN = 40


@dataclass
class Env:
    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings

    def worker(self) -> FusionWorker:  # a fresh instance == a process restart
        return FusionWorker(self.sessionmaker, self.settings)


@pytest_asyncio.fixture
async def env(postgres_url: str) -> AsyncIterator[Env]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=postgres_url,
        secret_key="x" * 16,
        fusion_enabled=True,
        fusion_warmup_minutes=WARMUP_MIN,
    )
    yield Env(sessionmaker=sessionmaker, settings=settings)
    await engine.dispose()


async def _seed_readings(
    env: Env, start: datetime, end: datetime, value: float, step_s: int = 15
) -> None:
    # 15 s cadence matches the ESPHome nodes (denser than the 30 s epoch grid, so every
    # epoch gets a sample — a slower cadence would leave gap epochs that read as quiet).
    async with env.sessionmaker() as s:
        t = start
        while t < end:
            s.add(
                SensorReading(
                    ts=t,
                    household_id="default",
                    device_id=1,
                    metric="movement",
                    value=value,
                    quality=0.9,
                )
            )
            s.add(
                SensorReading(
                    ts=t,
                    household_id="default",
                    device_id=1,
                    metric="presence",
                    value=1.0,
                    quality=0.9,
                )
            )
            t += timedelta(seconds=step_s)
        await s.commit()


async def _fused(env: Env) -> list[FusedState]:
    async with env.sessionmaker() as s:
        rows = await s.execute(select(FusedState).order_by(FusedState.ts, FusedState.id))
        return list(rows.scalars())


async def test_worker_records_sleep_then_wake(env: Env) -> None:
    now = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)
    # Asleep (low movement) for an hour, then awake (high movement) for the last 15 min.
    await _seed_readings(env, now - timedelta(minutes=60), now - timedelta(minutes=15), value=0.1)
    await _seed_readings(env, now - timedelta(minutes=15), now + timedelta(minutes=1), value=0.85)

    # Tick while still asleep, then after the wake — production ticks every 30 s, so each
    # transition is caught on the tick that follows it.
    await env.worker().tick(now - timedelta(minutes=15))
    await env.worker().tick(now)

    rows = await _fused(env)
    assert [r.sleep for r in rows] == ["sleep", "wake"]
    # The wake is timestamped near its true onset (~15 min before now), not on confirm.
    assert abs((rows[1].ts - (now - timedelta(minutes=15))).total_seconds()) <= 180
    # A single modality can't distress, even though movement is high.
    assert rows[1].arousal == "calm"
    assert "sensor" in rows[1].contributing_inputs


async def test_crash_recovery_session_survives_restart(env: Env) -> None:
    t0 = datetime(2026, 7, 13, 2, 0, tzinfo=UTC)
    # A continuous 90-minute sleep. Worker A observes the first half; a *restarted*
    # worker B observes the rest.
    await _seed_readings(env, t0, t0 + timedelta(minutes=90), value=0.1)

    await env.worker().tick(t0 + timedelta(minutes=45))  # worker A
    first = await _fused(env)
    assert [r.sleep for r in first] == ["sleep"]
    onset = first[0].ts

    await env.worker().tick(t0 + timedelta(minutes=90))  # worker B (a fresh instance)
    after = await _fused(env)
    # The restart re-derived the same ongoing sleep — no duplicate/spurious transition.
    assert [r.sleep for r in after] == ["sleep"]
    assert after[0].ts == onset

    # The derived session is one unbroken, still-open sleep spanning the restart.
    async with env.sessionmaker() as s:
        sessions = await sleep_sessions(s, "default", t0, t0 + timedelta(minutes=90))
    assert len(sessions) == 1
    assert sessions[0].ended_at is None
    assert sessions[0].started_at <= onset + timedelta(minutes=5)


async def test_signal_loader_carries_levels_forward_and_leaves_gaps(env: Env) -> None:
    window_start = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)
    async with env.sessionmaker() as s:
        # A movement_level "high" BEFORE the window (the seed) then "low" 5 min in.
        s.add(
            StateHistory(
                ts=window_start - timedelta(minutes=2),
                camera_id=1,
                state_type="movement_level",
                value="high",
                confidence=0.9,
            )
        )
        s.add(
            StateHistory(
                ts=window_start + timedelta(minutes=5),
                camera_id=1,
                state_type="movement_level",
                value="low",
                confidence=0.9,
            )
        )
        # One radar reading at +3 min (epoch 6 at 30 s epochs).
        s.add(
            SensorReading(
                ts=window_start + timedelta(minutes=3),
                household_id="default",
                device_id=1,
                metric="movement",
                value=0.5,
                quality=0.9,
            )
        )
        await s.commit()

    async with env.sessionmaker() as s:
        feats = await load_epoch_features(s, "default", window_start, n_epochs=20, epoch_seconds=30)

    assert feats[0].motion == 0.9  # carried forward from the pre-window seed (high)
    assert feats[9].motion == 0.9  # still high just before the "low" at epoch 10
    assert feats[10].motion == 0.1  # low takes effect
    assert feats[6].radar_move == 0.5  # the lone radar sample lands in epoch 6
    assert feats[0].radar_move is None  # a gap is None, not a fabricated zero
    assert "camera" in feats[6].inputs and "sensor" in feats[6].inputs


async def test_disabled_worker_does_nothing(env: Env) -> None:
    env.settings.fusion_enabled = False
    worker = env.worker()
    assert worker.enabled is False
    await worker.start()  # no task spawned
    await worker.stop()
    assert await _fused(env) == []


async def test_featurizer_surfaces_pulseox_hr(env: Env) -> None:
    # M4.2: HR reaches fusion only from the pulseox_readings table (accepted, i.e. already
    # quality-gated). Epochs with no pulse-ox stay hr=None, so a no-pulse-ox night is
    # unchanged.
    window_start = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)
    async with env.sessionmaker() as s:
        s.add(
            PulseOxReading(
                ts=window_start + timedelta(minutes=3),  # epoch 6 at 30 s epochs
                household_id="default",
                device_id=1,
                hr=178.0,
                spo2=97.0,
                perfusion=3.5,
                quality=0.9,
            )
        )
        await s.commit()
    async with env.sessionmaker() as s:
        feats = await load_epoch_features(
            s, "default", window_start, n_epochs=20, epoch_seconds=EPOCH_SECONDS
        )
    assert feats[6].hr == 178.0
    assert "pulseox" in feats[6].inputs
    assert feats[0].hr is None  # no pulse-ox sample in that epoch
