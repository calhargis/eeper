"""Persists movement-level state changes to state_history + events (M2.2).

The scoring loop calls this on each movement-level transition. The ``ts`` is set
by the caller at score time (not the insert time), so a row's timestamp is the
event time. The write is the source of truth and is ordered BEFORE the MQTT
publish, so a dead broker degrades to DB-only rather than losing the record.
Errors are logged and swallowed — a transient DB blip must never kill the scorer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api.models import Event, StateHistory

_log = logging.getLogger("eeper.insight.state_writer")
# Bound the write so a silently-partitioned DB connection (a hung commit that never
# raises) surfaces as a caught timeout instead of blocking the scorer loop forever.
_WRITE_TIMEOUT_SECONDS = 10.0


class StateWriter:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def write_movement_change(
        self,
        *,
        camera_id: int,
        ts: datetime,
        level: str,
        previous: str | None,
        confidence: float,
        contributing: list[str],
    ) -> bool:
        """Write one state_history row + one events row for a transition. Returns
        True on success, False if the write failed (already logged)."""
        try:
            async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS):
                async with self._sessionmaker() as session:
                    session.add(
                        StateHistory(
                            ts=ts,
                            camera_id=camera_id,
                            state_type="movement_level",
                            value=level,
                            confidence=confidence,
                            contributing_inputs=",".join(contributing),
                        )
                    )
                    session.add(
                        Event(
                            ts=ts,
                            camera_id=camera_id,
                            type="movement_level_change",
                            value=level,
                            previous_value=previous,
                            confidence=confidence,
                        )
                    )
                    await session.commit()
            return True
        except Exception:  # a DB blip (incl. a timed-out hang) must not stall the scorer
            _log.exception("failed to write movement change for camera %s", camera_id)
            return False
