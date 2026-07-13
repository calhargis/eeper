"""Fusion timeline API (M3.3): the Tonight view's scrubbable track.

Serves the fused sleep/wake + calm/distressed state as contiguous segments, plus the
consolidated sleep sessions, over a time window. Both are derived on read from the
durable ``fused_states`` transition log (see :mod:`eeper.api.fusion_read`), so they
always reflect the latest state and survive a worker restart. Nudge events are read
separately from ``GET /events`` and overlaid client-side.

Any authenticated household member can read; these are awareness signals, never a
medical, diagnostic, or vital-sign readout.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from eeper.api.dependencies import CurrentUser, NowDep, SessionDep
from eeper.api.fusion_read import sleep_sessions, timeline_segments
from eeper.api.schemas import FusedSegmentOut, SleepSessionOut, TonightTimelineOut

router = APIRouter(prefix="/fusion", tags=["fusion"])

_DEFAULT_WINDOW = timedelta(hours=12)  # a night; the UI can request a wider span
_MAX_WINDOW = timedelta(days=2)


@router.get("/timeline", response_model=TonightTimelineOut)
async def timeline(
    user: CurrentUser,
    session: SessionDep,
    now: NowDep,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> TonightTimelineOut:
    end = until or now
    start = since or end - _DEFAULT_WINDOW
    if end - start > _MAX_WINDOW:  # bound the scan
        start = end - _MAX_WINDOW

    segments = await timeline_segments(session, user.household_id, start, end)
    sessions = await sleep_sessions(session, user.household_id, start, end)
    return TonightTimelineOut(
        start=start,
        end=end,
        segments=[
            FusedSegmentOut(
                start=s.start,
                end=s.end,
                sleep=s.sleep.value,
                arousal=s.arousal.value,
                is_open=s.is_open,
            )
            for s in segments
        ],
        sessions=[SleepSessionOut(started_at=i.started_at, ended_at=i.ended_at) for i in sessions],
    )
