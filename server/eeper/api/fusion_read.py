"""Read helpers over the fused-state transition log (M3.3).

Sleep sessions are not stored as rows — they are a deterministic query over the durable
``fused_states`` transition log. That is what makes them survive a worker restart: the
transitions persist, so re-deriving the sessions after a restart yields the same result.
Slice 3's Tonight-timeline API reads through here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import FusedState
from eeper.fusion.model import DEFAULT_PARAMS, EPOCH_SECONDS, FusionParams, Sleep
from eeper.fusion.sessions import extract_sessions


@dataclass(frozen=True)
class SleepInterval:
    """A consolidated sleep period. ``ended_at`` is ``None`` for the session still in
    progress at the query's end."""

    started_at: datetime
    ended_at: datetime | None


async def _sleep_timeline(
    session: AsyncSession,
    household: str,
    start: datetime,
    end: datetime,
    epoch_seconds: int,
) -> list[Sleep]:
    """Reconstruct the per-epoch sleep/wake timeline over ``[start, end)`` by carrying
    each transition forward (defaulting to WAKE before the first known state)."""
    n = max(0, int((end - start).total_seconds() // epoch_seconds))
    seed = (
        await session.execute(
            select(FusedState.sleep)
            .where(FusedState.household_id == household, FusedState.ts < start)
            .order_by(FusedState.ts.desc(), FusedState.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    win = await session.execute(
        select(FusedState.ts, FusedState.sleep)
        .where(
            FusedState.household_id == household,
            FusedState.ts >= start,
            FusedState.ts < end,
        )
        .order_by(FusedState.ts, FusedState.id)
    )
    transitions: list[tuple[float, Sleep]] = []
    if seed is not None:
        transitions.append((-1.0, Sleep(seed)))
    transitions.extend(((ts - start).total_seconds(), Sleep(v)) for ts, v in win)

    out: list[Sleep] = []
    cur = Sleep.WAKE
    ti = 0
    for i in range(n):
        epoch_end = (i + 1) * epoch_seconds
        while ti < len(transitions) and transitions[ti][0] < epoch_end:
            cur = transitions[ti][1]
            ti += 1
        out.append(cur)
    return out


async def sleep_sessions(
    session: AsyncSession,
    household: str,
    start: datetime,
    end: datetime,
    epoch_seconds: int = EPOCH_SECONDS,
    params: FusionParams = DEFAULT_PARAMS,
) -> list[SleepInterval]:
    """Consolidated sleep sessions within ``[start, end)``, derived from the fused-state
    log. A session touching the last epoch is still open (``ended_at=None``)."""
    timeline = await _sleep_timeline(session, household, start, end, epoch_seconds)
    intervals: list[SleepInterval] = []
    for s in extract_sessions(timeline, params):
        started = start + timedelta(seconds=s.start_epoch * epoch_seconds)
        open_ended = s.end_epoch >= len(timeline)
        ended = None if open_ended else start + timedelta(seconds=s.end_epoch * epoch_seconds)
        intervals.append(SleepInterval(started_at=started, ended_at=ended))
    return intervals
