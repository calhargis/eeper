"""Recording controls — the admin-facing on/off switch for clip recording.

Deliberately a SETTING the recorder reads, never container orchestration. The api runs
hardened (non-root, read-only rootfs, ``cap_drop: ALL``, no Docker socket) and must never
gain the ability to start or stop containers — that would hand it root on the host. The
recorder polls this row on its normal reconcile tick and stops or respawns its ffmpeg
children accordingly, so the toggle takes effect within seconds with no privilege anywhere.

A missing row means ENABLED (see ``RecordingSettings``), so an upgrade keeps recording as it
was and a fresh install records out of the box.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.dependencies import AdminUser, CurrentUser, SessionDep
from eeper.api.models import RecordingSettings
from eeper.api.recording_settings import read_recording_config
from eeper.api.schemas import RecordingSettingsIn, RecordingSettingsOut

router = APIRouter(prefix="/recording", tags=["recording"])


async def read_settings(session: AsyncSession, household_id: str) -> RecordingSettingsOut:
    """The household's settings as the API shape. The defaults (and the missing-row rule)
    live in `recording_settings` so the recorder and insight engine share exactly one
    definition of "is recording on?"""
    cfg = await read_recording_config(session, household_id)
    return RecordingSettingsOut(
        recording_enabled=cfg.recording_enabled,
        storage_target_id=cfg.storage_target_id,
        updated_at=cfg.updated_at,
    )


@router.get("/settings", response_model=RecordingSettingsOut)
async def get_recording_settings(user: CurrentUser, session: SessionDep) -> RecordingSettingsOut:
    """Readable by any signed-in member so the UI can explain why clips are (or aren't)
    being captured. Changing it is admin-only."""
    return await read_settings(session, user.household_id)


@router.patch("/settings", response_model=RecordingSettingsOut)
async def update_recording_settings(
    body: RecordingSettingsIn, admin: AdminUser, session: SessionDep
) -> RecordingSettingsOut:
    """Partial update, admin-only. Absent fields are left untouched, so a client that only
    knows about one field can't silently reset the others."""
    current = await read_settings(session, admin.household_id)
    enabled = (
        current.recording_enabled if body.recording_enabled is None else body.recording_enabled
    )
    target = current.storage_target_id if body.storage_target_id is None else body.storage_target_id

    await session.execute(
        insert(RecordingSettings)
        .values(
            household_id=admin.household_id,
            recording_enabled=enabled,
            storage_target_id=target,
            updated_by=admin.id,
        )
        .on_conflict_do_update(
            index_elements=["household_id"],
            set_={
                "recording_enabled": enabled,
                "storage_target_id": target,
                "updated_by": admin.id,
                "updated_at": func.now(),
            },
        )
    )
    await session.commit()
    return await read_settings(session, admin.household_id)
