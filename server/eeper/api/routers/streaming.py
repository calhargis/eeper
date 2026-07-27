"""Presence-gated streaming — stop the camera when nobody is in the crib.

Power saving with a hard constraint: the camera must never be off when the baby might be
there. Every uncertain case in :func:`~eeper.api.stream_gating.read_gate` therefore resolves
to "keep streaming", and this router is only the control surface over that decision.

The feature is HIDDEN, not merely inert, when nothing can answer the presence question — a
toggle that silently does nothing is worse than an absent one, because it implies the camera
will come back on when a baby is detected by something that does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep
from eeper.api.models import StreamGating
from eeper.api.schemas import (
    PresenceOut,
    StreamGatingIn,
    StreamGatingOut,
    StreamOverrideIn,
)
from eeper.api.stream_gating import read_gate

router = APIRouter(prefix="/streaming", tags=["streaming"])


async def _status(session: AsyncSession, household_id: str) -> StreamGatingOut:
    gate = await read_gate(session, household_id)
    return StreamGatingOut(
        enabled=gate.enabled,
        available=gate.available,
        streaming=gate.should_stream,
        reason=gate.reason,
        override_until=gate.override_until,
        presence=PresenceOut(
            available=gate.presence.available,
            present=gate.presence.present,
            stale=gate.presence.stale,
            last_seen=gate.presence.last_seen,
            confidence=gate.presence.confidence,
        ),
    )


async def _upsert(
    session: AsyncSession,
    household_id: str,
    user_id: int,
    *,
    enabled: bool,
    override_until: datetime | None,
) -> None:
    await session.execute(
        insert(StreamGating)
        .values(
            household_id=household_id,
            enabled=enabled,
            override_until=override_until,
            updated_by=user_id,
        )
        .on_conflict_do_update(
            index_elements=["household_id"],
            set_={
                "enabled": enabled,
                "override_until": override_until,
                "updated_by": user_id,
                "updated_at": func.now(),
            },
        )
    )
    await session.commit()


@router.get("/status", response_model=StreamGatingOut)
async def get_streaming_status(user: CurrentUser, session: SessionDep) -> StreamGatingOut:
    """Readable by any signed-in member: the Live view needs it to explain a dark screen
    rather than just showing one."""
    return await _status(session, user.household_id)


@router.patch("/settings", response_model=StreamGatingOut)
async def update_streaming_settings(
    body: StreamGatingIn, admin: AdminUser, session: SessionDep
) -> StreamGatingOut:
    """Turn presence gating on or off. Admin-only — it decides whether a camera may switch
    itself off, which is not a per-viewer preference."""
    current = await read_gate(session, admin.household_id)
    if body.enabled and not current.available:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No paired input can detect presence, so the camera could never be switched back "
            "on automatically. Pair a thermal node first.",
        )
    enabled = current.enabled if body.enabled is None else body.enabled
    # Turning the gate OFF clears any override: it exists to escape the gate, and leaving a
    # stale expiry behind would silently suppress the next enable.
    override = current.override_until if enabled else None
    await _upsert(session, admin.household_id, admin.id, enabled=enabled, override_until=override)
    return await _status(session, admin.household_id)


@router.post("/override", response_model=StreamGatingOut)
async def start_anyway(
    body: StreamOverrideIn, user: CurrentUser, session: SessionDep
) -> StreamGatingOut:
    """ "Start anyway" — watch an empty crib on purpose.

    Deliberately available to ANY member, not just admins: a viewer looking at "no baby
    detected" must be able to see for themselves. Turning the gating on and off is the
    admin decision; overriding it for the next half hour is not.
    """
    current = await read_gate(session, user.household_id)
    if not current.enabled:
        # Nothing to override — say so rather than storing an expiry that does nothing.
        raise HTTPException(status.HTTP_409_CONFLICT, "Presence-gated streaming is not enabled.")
    until = datetime.now(UTC) + timedelta(minutes=body.minutes)
    await _upsert(
        session, user.household_id, user.id, enabled=current.enabled, override_until=until
    )
    return await _status(session, user.household_id)


@router.delete("/override", response_model=StreamGatingOut)
async def stop_override(user: CurrentUser, session: SessionDep) -> StreamGatingOut:
    """End the override early and hand control back to the presence gate."""
    current = await read_gate(session, user.household_id)
    await _upsert(session, user.household_id, user.id, enabled=current.enabled, override_until=None)
    return await _status(session, user.household_id)
