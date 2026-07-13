"""Trends API (M4.1): sleep rollups + CSV export.

Reads the ``trends_nightly`` continuous aggregate (materialized nightly rollups of the
``sleep_sessions`` hypertable, real-time so recent nights are included). Nightly and
weekly series back the Trends charts; CSV export is admin-only (a viewer can view but
not export). These are awareness metrics — sleep durations and wake counts — never a
medical or vital-sign readout.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.dependencies import AdminUser, CurrentUser, NowDep, SessionDep
from eeper.api.schemas import TrendNight, TrendWeek

router = APIRouter(prefix="/trends", tags=["trends"])

_MAX_DAYS = 730  # bound the scan; the UI never needs more than ~2 years

_NIGHTLY_SQL = text(
    "SELECT night, sessions, total_sleep_s, wakes, longest_stretch_s "
    "FROM trends_nightly "
    "WHERE household_id = :hh AND night >= :since AND night < :until "
    "ORDER BY night"
)
_WEEKLY_SQL = text(
    "SELECT time_bucket('7 days', night) AS week, count(*) AS nights, "
    "sum(total_sleep_s) AS total_sleep_s, avg(total_sleep_s) AS avg_sleep_s, "
    "sum(wakes) AS wakes, max(longest_stretch_s) AS longest_stretch_s "
    "FROM trends_nightly "
    "WHERE household_id = :hh AND night >= :since AND night < :until "
    "GROUP BY week ORDER BY week"
)


def _window(
    now: datetime, since: datetime | None, until: datetime | None, default_days: int
) -> tuple[datetime, datetime]:
    end = until or now
    start = since or end - timedelta(days=default_days)
    if end - start > timedelta(days=_MAX_DAYS):
        start = end - timedelta(days=_MAX_DAYS)
    return start, end


async def _nightly(
    session: AsyncSession, household: str, start: datetime, end: datetime
) -> list[TrendNight]:
    rows = await session.execute(_NIGHTLY_SQL, {"hh": household, "since": start, "until": end})
    return [
        TrendNight(
            night=r.night,
            sessions=r.sessions,
            total_sleep_s=r.total_sleep_s,
            wakes=r.wakes,
            longest_stretch_s=r.longest_stretch_s,
        )
        for r in rows
    ]


@router.get("/nightly", response_model=list[TrendNight])
async def nightly(
    user: CurrentUser,
    session: SessionDep,
    now: NowDep,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> list[TrendNight]:
    start, end = _window(now, since, until, default_days=30)
    return await _nightly(session, user.household_id, start, end)


@router.get("/weekly", response_model=list[TrendWeek])
async def weekly(
    user: CurrentUser,
    session: SessionDep,
    now: NowDep,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> list[TrendWeek]:
    start, end = _window(now, since, until, default_days=84)  # ~12 weeks
    rows = await session.execute(
        _WEEKLY_SQL, {"hh": user.household_id, "since": start, "until": end}
    )
    return [
        TrendWeek(
            week=r.week,
            nights=r.nights,
            total_sleep_s=r.total_sleep_s,
            avg_sleep_s=r.avg_sleep_s,
            wakes=r.wakes,
            longest_stretch_s=r.longest_stretch_s,
        )
        for r in rows
    ]


@router.get("/export.csv")
async def export_csv(
    admin: AdminUser,  # a viewer is denied (403) before any query runs
    session: SessionDep,
    now: NowDep,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> Response:
    """Nightly sleep data as CSV. Admin-only — a viewer role cannot export."""
    start, end = _window(now, since, until, default_days=90)
    nights = await _nightly(session, admin.household_id, start, end)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["night", "sessions", "total_sleep_hours", "wakes", "longest_stretch_hours"])
    for n in nights:
        writer.writerow(
            [
                n.night.date().isoformat(),
                n.sessions,
                round(n.total_sleep_s / 3600, 2),
                n.wakes,
                round(n.longest_stretch_s / 3600, 2),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": 'attachment; filename="eeper-sleep-trends.csv"'},
    )
