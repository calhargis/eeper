"""Is anyone in the crib, and can we even tell?

Two questions, deliberately separate. "Is a presence-capable input CONNECTED" decides whether
the presence-gated streaming feature is offered at all — with no such input the whole feature
is hidden rather than silently gating on a signal that will never arrive. "Is someone THERE
right now" is only asked once the first is true.

The presence signal itself is already debounced at its source (the thermal node applies
contrast + time hysteresis before it publishes — see eeper/thermal/features.py), so this
module reads a stable answer rather than re-deriving one. What it adds is FRESHNESS: a node
that stopped publishing an hour ago must not read as "no baby", because absent evidence and
evidence of absence are not the same thing, and confusing them here would switch a camera off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import Device, ThermalFeaturesReading

# Device kinds that can answer "is someone in the crib". Thermal is the only one today; a
# mmWave/radar node reporting occupancy would join this set, which is why the feature is
# written against "a presence-capable input" and not against "the thermal camera".
PRESENCE_KINDS = ("thermal",)

# A reading older than this tells us nothing about now. Chosen well above the node's 1 Hz
# publish cadence so an ordinary hiccup doesn't read as a stale sensor.
FRESH_SECONDS = 90.0


@dataclass(frozen=True)
class PresenceState:
    """What the presence inputs can currently tell us."""

    available: bool  # a presence-capable device is paired
    present: bool | None  # None = we cannot currently tell (no device, or stale)
    stale: bool  # a device exists but hasn't reported recently
    last_seen: datetime | None
    confidence: float | None

    @property
    def known_absent(self) -> bool:
        """True only when a working input actively reports an empty crib. This — never
        ``not present`` — is what may switch a camera off: `present is None` means we do not
        know, and not knowing must always keep the monitor running."""
        return self.available and not self.stale and self.present is False


async def presence_sources(session: AsyncSession, household_id: str) -> list[Device]:
    """Paired devices that can answer the presence question."""
    rows = await session.execute(
        select(Device).where(Device.household_id == household_id, Device.kind.in_(PRESENCE_KINDS))
    )
    return list(rows.scalars().all())


async def read_presence(
    session: AsyncSession, household_id: str, now: datetime | None = None
) -> PresenceState:
    """The household's current presence answer, with an explicit "we can't tell" case."""
    now = now or datetime.now(UTC)
    sources = await presence_sources(session, household_id)
    if not sources:
        return PresenceState(
            available=False, present=None, stale=False, last_seen=None, confidence=None
        )

    latest = (
        await session.execute(
            select(ThermalFeaturesReading)
            .where(
                ThermalFeaturesReading.household_id == household_id,
                ThermalFeaturesReading.device_id.in_([d.id for d in sources]),
            )
            .order_by(ThermalFeaturesReading.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if latest is None:
        # Paired but never reported — available, but nothing to say yet.
        return PresenceState(
            available=True, present=None, stale=True, last_seen=None, confidence=None
        )
    stale = (now - latest.ts) > timedelta(seconds=FRESH_SECONDS)
    return PresenceState(
        available=True,
        present=None if stale else latest.presence,
        stale=stale,
        last_seen=latest.ts,
        confidence=None if stale else latest.presence_confidence,
    )
