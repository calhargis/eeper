"""Shared read path for the household's recording settings.

Lives outside the router so the recorder and the insight engine can consult the flag
without importing FastAPI routing. Every reader MUST go through here, because the
missing-row default is the contract: no row means recording is ENABLED, which is what keeps
an upgrade behaving exactly as it did before this setting existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.config import Settings
from eeper.api.models import RecordingSettings
from eeper.api.storage import StorageTarget, parse_targets, resolve_media_root

# Applied when the household has no row yet.
DEFAULT_RECORDING_ENABLED = True
DEFAULT_STORAGE_TARGET = "internal"
# The recorder and insight engine are single-household today (see Camera/Event defaults).
DEFAULT_HOUSEHOLD = "default"


@dataclass(frozen=True)
class RecordingConfig:
    recording_enabled: bool
    storage_target_id: str
    updated_at: datetime | None = None


async def read_recording_config(session: AsyncSession, household_id: str) -> RecordingConfig:
    """The household's settings, or the defaults when no row exists. Never writes — a read
    must not materialise a row, so an untouched deployment keeps the defaults."""
    row = (
        await session.execute(
            select(RecordingSettings).where(RecordingSettings.household_id == household_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return RecordingConfig(
            recording_enabled=DEFAULT_RECORDING_ENABLED,
            storage_target_id=DEFAULT_STORAGE_TARGET,
        )
    return RecordingConfig(
        recording_enabled=row.recording_enabled,
        storage_target_id=row.storage_target_id,
        updated_at=row.updated_at,
    )


async def is_recording_enabled(
    session: AsyncSession, household_id: str = DEFAULT_HOUSEHOLD
) -> bool:
    """Convenience for the hot paths (recorder reconcile, clip-intent gate)."""
    return (await read_recording_config(session, household_id)).recording_enabled


def declared_targets(settings: Settings) -> list[StorageTarget]:
    """The storage targets this deployment's operator has declared, always including the
    built-in media volume. This list is the ONLY thing a stored target id is resolved
    against — see eeper/api/storage.py for why the app can't discover disks itself."""
    return parse_targets(settings.storage_targets, settings.media_root)


async def read_media_root(
    session: AsyncSession, settings: Settings, household_id: str = DEFAULT_HOUSEHOLD
) -> str:
    """Where recordings and clips go right now: the selected target's path, or the built-in
    root when the selection names a target this deployment no longer declares.

    Segments and promoted clips share this root by necessity — clip promotion hard-links
    covering segments into the clip dir (clip_service.py), and a hard link cannot cross
    filesystems (EXDEV). So the setting picks one root for both, not two independent dirs.
    """
    cfg = await read_recording_config(session, household_id)
    return resolve_media_root(
        declared_targets(settings), cfg.storage_target_id, settings.media_root
    )
