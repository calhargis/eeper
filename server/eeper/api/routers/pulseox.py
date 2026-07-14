"""Pulse-oximetry gating (M4.2 slice 1).

Pulse-ox is OPTIONAL and INSIGHTS-ONLY, and stays fully inert until BOTH the `pulseox`
Compose profile is enabled AND an admin has acknowledged the current disclaimer. This
router is the gate: it reports the state, serves the disclaimer, and records an admin's
acknowledgment. The data path (ingestion, fusion features, UI) is later slices and each
checks ``enabled`` here. eeper is never a vital-sign monitor or alarm.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep, SettingsDep
from eeper.api.models import PulseOxConsent
from eeper.api.pulseox_copy import (
    ACCURACY_CAVEAT,
    DISCLAIMER_TEXT,
    DISCLAIMER_VERSION,
    SAFE_SLEEP_URL,
)
from eeper.api.schemas import (
    PulseOxAcknowledge,
    PulseOxDeviceHealth,
    PulseOxDisclaimer,
    PulseOxStatus,
)

router = APIRouter(prefix="/pulseox", tags=["pulseox"])


async def _acknowledged(session: SessionDep, household_id: str) -> bool:
    """True iff an acknowledgment of the CURRENT disclaimer version exists — an older
    acknowledgment (before the text was updated) does not count."""
    row = await session.execute(
        select(PulseOxConsent.disclaimer_version).where(PulseOxConsent.household_id == household_id)
    )
    return row.scalar_one_or_none() == DISCLAIMER_VERSION


@router.get("/status", response_model=PulseOxStatus)
async def status_(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> PulseOxStatus:
    profile = settings.pulseox_profile_enabled
    acknowledged = await _acknowledged(session, user.household_id)
    return PulseOxStatus(
        profile_enabled=profile,
        acknowledged=acknowledged,
        enabled=profile and acknowledged,  # the gate: both halves required
        disclaimer_version=DISCLAIMER_VERSION,
    )


@router.get("/health", response_model=list[PulseOxDeviceHealth])
async def health(
    request: Request, user: CurrentUser, settings: SettingsDep
) -> list[PulseOxDeviceHealth]:
    """Per-device pulse-ox ingest stats so the quality-gate discard rate is observable.
    Inert (empty) when the profile is off."""
    if not settings.pulseox_profile_enabled:
        return []
    ingestor = getattr(request.app.state, "pulseox", None)
    stats: dict[int, tuple[int, int]] = ingestor.stats() if ingestor is not None else {}
    out: list[PulseOxDeviceHealth] = []
    for device_id, (accepted, discarded) in sorted(stats.items()):
        total = accepted + discarded
        out.append(
            PulseOxDeviceHealth(
                device_id=device_id,
                accepted=accepted,
                discarded=discarded,
                discard_rate=discarded / total if total else 0.0,
            )
        )
    return out


@router.get("/disclaimer", response_model=PulseOxDisclaimer)
async def disclaimer(user: CurrentUser) -> PulseOxDisclaimer:
    return PulseOxDisclaimer(
        version=DISCLAIMER_VERSION,
        text=DISCLAIMER_TEXT,
        accuracy_caveat=ACCURACY_CAVEAT,
        safe_sleep_url=SAFE_SLEEP_URL,
    )


@router.post("/acknowledge", response_model=PulseOxStatus)
async def acknowledge(
    body: PulseOxAcknowledge, admin: AdminUser, session: SessionDep, settings: SettingsDep
) -> PulseOxStatus:
    """An admin acknowledges the disclaimer for their household. Requires the profile to
    be enabled on the deployment (no point acknowledging otherwise), and the acknowledged
    version must be the current one."""
    if not settings.pulseox_profile_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The pulse-ox profile is not enabled on this deployment.",
        )
    if body.version != DISCLAIMER_VERSION:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Disclaimer version mismatch — the current version is {DISCLAIMER_VERSION}.",
        )
    await session.execute(
        insert(PulseOxConsent)
        .values(
            household_id=admin.household_id,
            disclaimer_version=DISCLAIMER_VERSION,
            acknowledged_by=admin.id,
        )
        .on_conflict_do_update(
            index_elements=["household_id"],
            set_={
                "disclaimer_version": DISCLAIMER_VERSION,
                "acknowledged_by": admin.id,
                "acknowledged_at": func.now(),
            },
        )
    )
    await session.commit()
    return PulseOxStatus(
        profile_enabled=True,
        acknowledged=True,
        enabled=True,
        disclaimer_version=DISCLAIMER_VERSION,
    )
