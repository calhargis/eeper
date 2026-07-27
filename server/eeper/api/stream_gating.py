"""Should the camera be streaming right now?

The one place that question is answered, so the camera monitor, the recorder and the UI can
never disagree about it. Every reader goes through :func:`read_gate`.

The rule is deliberately biased. Streaming stops ONLY when a working presence input actively
reports an empty crib. No input, a stale input, a gate that was never enabled, or an active
"Start anyway" override all keep the camera live. Every uncertain case resolves to *keep
monitoring*, because the failure that matters is a parent looking at a dark screen while
their child is in the crib.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.models import StreamGating
from eeper.api.presence import PresenceState, read_presence

# A missing row means the feature is OFF: it turns a camera off, so it must never enable
# itself on upgrade.
DEFAULT_ENABLED = False
DEFAULT_HOUSEHOLD = "default"

# How long "Start anyway" holds the stream up. Long enough to feed, change and settle a baby
# without the stream dropping mid-task; short enough that a forgotten override lapses.
DEFAULT_OVERRIDE_MINUTES = 30


@dataclass(frozen=True)
class GateDecision:
    should_stream: bool
    enabled: bool  # is the feature switched on for this household
    available: bool  # is there a presence-capable input at all
    presence: PresenceState
    override_until: datetime | None
    # Why the stream is in the state it is, for the UI to explain rather than just obey.
    reason: str  # streaming | no_presence | override | disabled | unavailable | unknown

    @property
    def override_active(self) -> bool:
        return self.reason == "override"


async def read_gate(
    session: AsyncSession, household_id: str = DEFAULT_HOUSEHOLD, now: datetime | None = None
) -> GateDecision:
    now = now or datetime.now(UTC)
    row = (
        await session.execute(select(StreamGating).where(StreamGating.household_id == household_id))
    ).scalar_one_or_none()
    enabled = DEFAULT_ENABLED if row is None else row.enabled
    override_until = row.override_until if row is not None else None
    presence = await read_presence(session, household_id, now=now)

    def decide(should: bool, reason: str) -> GateDecision:
        return GateDecision(
            should_stream=should,
            enabled=enabled,
            available=presence.available,
            presence=presence,
            override_until=override_until,
            reason=reason,
        )

    if not enabled:
        return decide(True, "disabled")
    if not presence.available:
        # The operator enabled gating and then unpaired the sensor. Keep streaming and let the
        # UI hide the feature — never gate on a signal that cannot arrive.
        return decide(True, "unavailable")
    if override_until is not None and override_until > now:
        return decide(True, "override")
    if presence.known_absent:
        return decide(False, "no_presence")
    if presence.present:
        return decide(True, "streaming")
    # Stale or not-yet-reported: we do not know, so we keep watching.
    return decide(True, "unknown")
