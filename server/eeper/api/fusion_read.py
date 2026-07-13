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
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import FusedState, SleepSessionRecord
from eeper.fusion.model import DEFAULT_PARAMS, EPOCH_SECONDS, Arousal, FusionParams, Sleep
from eeper.fusion.sessions import extract_sessions


@dataclass(frozen=True)
class SleepInterval:
    """A consolidated sleep period. ``ended_at`` is ``None`` for the session still in
    progress at the query's end."""

    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class StateSegment:
    """A span of constant fused state, for the Tonight timeline track. ``is_open`` marks
    the still-ongoing final segment (its ``end`` is the query end, not a transition)."""

    start: datetime
    end: datetime
    sleep: Sleep
    arousal: Arousal
    is_open: bool


async def timeline_segments(
    session: AsyncSession, household: str, start: datetime, end: datetime
) -> list[StateSegment]:
    """The fused-state timeline over ``[start, end)`` as contiguous segments — the state
    that held at ``start`` (from the last transition at or before it), then one segment
    per transition, the last extending to ``end``."""
    if end <= start:
        return []
    seed = (
        await session.execute(
            select(FusedState.sleep, FusedState.arousal)
            .where(FusedState.household_id == household, FusedState.ts <= start)
            .order_by(FusedState.ts.desc(), FusedState.id.desc())
            .limit(1)
        )
    ).first()
    win = await session.execute(
        select(FusedState.ts, FusedState.sleep, FusedState.arousal)
        .where(
            FusedState.household_id == household,
            FusedState.ts > start,
            FusedState.ts < end,
        )
        .order_by(FusedState.ts, FusedState.id)
    )
    cur_sleep = Sleep(seed.sleep) if seed is not None else Sleep.WAKE
    cur_arousal = Arousal(seed.arousal) if seed is not None else Arousal.CALM
    seg_start = start
    segments: list[StateSegment] = []
    for ts, sleep, arousal in win:
        segments.append(StateSegment(seg_start, ts, cur_sleep, cur_arousal, is_open=False))
        cur_sleep, cur_arousal = Sleep(sleep), Arousal(arousal)
        seg_start = ts
    segments.append(StateSegment(seg_start, end, cur_sleep, cur_arousal, is_open=True))
    return segments


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


def session_metrics(segments: list[StateSegment]) -> tuple[float, int, float]:
    """Per-session Trends metrics from its fused-state segments: total time asleep, the
    number of intra-session awakenings (bridged brief wakes), and the longest unbroken
    sleep span (all seconds)."""
    total_sleep = 0.0
    longest = 0.0
    wakes = 0
    for seg in segments:
        dur = (seg.end - seg.start).total_seconds()
        if seg.sleep is Sleep.SLEEP:
            total_sleep += dur
            longest = max(longest, dur)
        else:
            wakes += 1
    return total_sleep, wakes, longest


async def materialize_closed_sessions(
    session: AsyncSession, household: str, start: datetime, end: datetime
) -> int:
    """Write a ``sleep_sessions`` row for each CLOSED session in ``[start, end)`` (the
    still-open one is left to be derived on read). Idempotent via
    ``ON CONFLICT (household_id, started_at) DO NOTHING``, so every cycle can re-run over
    the same lookback harmlessly. Returns the number of rows inserted."""
    written = 0
    for s in await sleep_sessions(session, household, start, end):
        if s.ended_at is None:
            continue  # open session — not materialized until it closes
        segments = await timeline_segments(session, household, s.started_at, s.ended_at)
        total_sleep_s, wake_count, longest_stretch_s = session_metrics(segments)
        result = await session.execute(
            insert(SleepSessionRecord)
            .values(
                started_at=s.started_at,
                household_id=household,
                ended_at=s.ended_at,
                total_sleep_s=total_sleep_s,
                wake_count=wake_count,
                longest_stretch_s=longest_stretch_s,
            )
            .on_conflict_do_nothing(index_elements=["household_id", "started_at"])
            .returning(SleepSessionRecord.id)
        )
        written += len(result.scalars().all())  # empty on a conflict (already materialized)
    return written


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
