"""Live fusion worker (M3.3).

Every cycle it re-runs the pure fusion over a warmup window of each household's
persisted signals (seeded from the last persisted state) and appends a ``fused_states``
row whenever the current sleep/wake or calm/distressed state has changed. It holds NO
in-memory state between cycles: the DB (signals + the transition log) is the only state,
so a restart re-derives the current state from the same window and sleep sessions —
which are a query over ``fused_states`` — survive the restart unbroken.

Awareness states only; never a medical, diagnostic, or vital-sign readout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.config import Settings
from eeper.api.fusion_read import materialize_closed_sessions
from eeper.api.fusion_signals import active_households, load_epoch_features
from eeper.api.models import FusedState
from eeper.fusion.model import DEFAULT_PARAMS, EPOCH_SECONDS, Arousal, Sleep
from eeper.fusion.sessions import backdate_transitions
from eeper.fusion.state import run

_log = logging.getLogger("eeper.api.fusion_worker")


class FusionWorker:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._params = DEFAULT_PARAMS
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.fusion_enabled

    async def start(self) -> None:
        if not self.enabled:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick(datetime.now(UTC))
            except Exception:  # a transient DB blip must never kill the worker
                _log.exception("fusion tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.fusion_interval_seconds
                )

    async def tick(self, now: datetime) -> None:
        """One fusion pass over every active household. Public so tests can drive it
        deterministically without the loop."""
        n_epochs = max(1, self._settings.fusion_warmup_minutes * 60 // EPOCH_SECONDS)
        window_start = now - timedelta(seconds=n_epochs * EPOCH_SECONDS)
        materialize_start = now - timedelta(hours=self._settings.fusion_materialize_lookback_hours)
        async with self._sessionmaker() as session:
            for household in await active_households(session, window_start):
                await self._fuse(session, household, window_start, n_epochs, now)
                # Persist any session that has closed since last cycle (the Trends source).
                await materialize_closed_sessions(session, household, materialize_start, now)
            await session.commit()

    async def _fuse(
        self,
        session: AsyncSession,
        household: str,
        window_start: datetime,
        n_epochs: int,
        now: datetime,
    ) -> None:
        features = await load_epoch_features(
            session, household, window_start, n_epochs, EPOCH_SECONDS
        )
        last = await self._last_state(session, household)  # for the change comparison
        # Seed the window from the state that held AT its start (not the most recent one,
        # which may be a transition inside the window), so the replay evolves faithfully.
        initial = await self._sleep_at(session, household, window_start)
        states = run(features, self._params, initial=initial)
        if not states:
            return

        # Back-date the sleep timeline so a transition is timestamped at its true onset,
        # not when the sustain confirmed it (keeps session boundaries within tolerance).
        sleep_bd = backdate_transitions([s.sleep for s in states], self._params)
        current_sleep = sleep_bd[-1]
        current = states[-1]
        arousal = current.arousal

        # The implicit pre-history state is WAKE + CALM, so don't anchor it as a row.
        if last is None and current_sleep is Sleep.WAKE and arousal is Arousal.CALM:
            return
        if last is not None and last.sleep == current_sleep and last.arousal == arousal:
            return  # unchanged — nothing to append

        # Timestamp: a sleep change lands at its back-dated onset; an arousal-only change
        # lands at the current epoch. Clamp strictly after the last row (monotonic PK).
        if last is None or last.sleep != current_sleep:
            onset_epoch = _last_transition_index(sleep_bd)
            ts = window_start + timedelta(seconds=onset_epoch * EPOCH_SECONDS)
        else:
            ts = now - timedelta(seconds=EPOCH_SECONDS)
        if last is not None and ts <= last.ts:
            ts = last.ts + timedelta(seconds=1)

        session.add(
            FusedState(
                ts=ts,
                household_id=household,
                sleep=current_sleep.value,
                arousal=arousal.value,
                activity=current.activity,
                confidence=current.confidence,
                contributing_inputs=",".join(current.inputs),
            )
        )

    async def _last_state(self, session: AsyncSession, household: str) -> FusedState | None:
        row = await session.execute(
            select(FusedState)
            .where(FusedState.household_id == household)
            .order_by(FusedState.ts.desc(), FusedState.id.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def _sleep_at(self, session: AsyncSession, household: str, ts: datetime) -> Sleep:
        """The sleep state in effect at ``ts`` (the last transition at or before it);
        WAKE if the household has no prior fused state."""
        val = (
            await session.execute(
                select(FusedState.sleep)
                .where(FusedState.household_id == household, FusedState.ts <= ts)
                .order_by(FusedState.ts.desc(), FusedState.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return Sleep(val) if val is not None else Sleep.WAKE


def _last_transition_index(states: list[Sleep]) -> int:
    """Index of the last change in a sleep timeline (0 if it never changes) — the onset
    of the current run."""
    for i in range(len(states) - 1, 0, -1):
        if states[i] is not states[i - 1]:
            return i
    return 0
