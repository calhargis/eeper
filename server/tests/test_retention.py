"""M4.3 media retention matrix: recording segments are evicted by AGE (older than
``media_max_age_seconds``, if set) and by QUOTA (oldest-first once over the byte budget).
Across every combination the invariants hold: the newest-per-camera (active) segment is
never touched, and promoted clips are never scanned/evicted.

Pure — a real on-disk media tree in a tmp dir, no DB or ffmpeg.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from eeper.api.config import Settings
from eeper.recorder.layout import SEG_SUFFIX, clips_dir, seg_dir
from eeper.recorder.retention import evict_once

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _settings(root: Path, *, quota: int, max_age_s: int = 0) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x/x",
        secret_key="x" * 16,
        media_root=str(root),
        media_quota_bytes=quota,
        media_max_age_seconds=max_age_s,
    )


def _seg(root: Path, camera_id: int, minutes_ago: int, size: int) -> Path:
    """Write a segment file whose strftime name encodes its start time."""
    d = seg_dir(str(root), camera_id)
    d.mkdir(parents=True, exist_ok=True)
    start = _NOW - timedelta(minutes=minutes_ago)
    path = d / f"{start.strftime('%Y%m%d-%H%M%S')}{SEG_SUFFIX}"
    path.write_bytes(b"\0" * size)
    return path


def _names(root: Path, camera_id: int) -> set[str]:
    d = seg_dir(str(root), camera_id)
    return {p.name for p in d.glob(f"*{SEG_SUFFIX}")} if d.exists() else set()


def test_quota_evicts_oldest_first_keeps_active(tmp_path: Path) -> None:
    # cam0: 3 finalized (100B each, ages 30/20/10) + 1 active (age 0), 400B total. Quota
    # 350B: drop the single oldest finalized to get under, and never the newest (active).
    old = _seg(tmp_path, 0, 30, 100)
    mid = _seg(tmp_path, 0, 20, 100)
    recent = _seg(tmp_path, 0, 10, 100)
    active = _seg(tmp_path, 0, 0, 100)

    evict_once(_settings(tmp_path, quota=350), now=_NOW)

    assert not old.exists()  # oldest evicted
    assert mid.exists() and recent.exists()
    assert active.exists()  # newest-per-camera never touched


def test_quota_never_evicts_the_only_segment(tmp_path: Path) -> None:
    # A single (therefore active) segment over quota must survive — there is no finalized
    # segment to drop, and the active one is off-limits.
    only = _seg(tmp_path, 0, 0, 10_000)
    evict_once(_settings(tmp_path, quota=1), now=_NOW)
    assert only.exists()


def test_age_evicts_old_finalized_regardless_of_quota(tmp_path: Path) -> None:
    # Quota is huge (no quota pressure); a 15-min age bound drops the two oldest but keeps
    # the fresh finalized one and the active one.
    s30 = _seg(tmp_path, 0, 30, 100)
    s20 = _seg(tmp_path, 0, 20, 100)
    s05 = _seg(tmp_path, 0, 5, 100)
    active = _seg(tmp_path, 0, 0, 100)

    evict_once(_settings(tmp_path, quota=10**12, max_age_s=15 * 60), now=_NOW)

    assert not s30.exists() and not s20.exists()  # older than 15 min → gone
    assert s05.exists()  # within the age window
    assert active.exists()  # active always kept


def test_age_never_evicts_the_active_segment_even_if_old(tmp_path: Path) -> None:
    # The active (newest-per-camera) segment is excluded from BOTH policies, so even an
    # ancient lone segment survives the age pass.
    only = _seg(tmp_path, 0, 999, 100)
    evict_once(_settings(tmp_path, quota=10**12, max_age_s=60), now=_NOW)
    assert only.exists()


def test_age_then_quota_compose(tmp_path: Path) -> None:
    # Five finalized (100B, ages 50/40/30/20/10) + active. Age 35 min drops 50 & 40.
    # Remaining finalized+active = 400B; quota 250B then drops oldest-remaining (30, 20)
    # until under, leaving age-10 finalized + active.
    for m in (50, 40, 30, 20, 10):
        _seg(tmp_path, 0, m, 100)
    _seg(tmp_path, 0, 0, 100)  # active

    evict_once(_settings(tmp_path, quota=250, max_age_s=35 * 60), now=_NOW)

    remaining = _names(tmp_path, 0)
    assert len(remaining) == 2  # age-10 finalized + active
    kept_starts = sorted(remaining)
    # The two survivors are the two newest by name (strftime sorts chronologically).
    assert kept_starts == sorted(remaining)[-2:]


def test_eviction_spans_cameras_oldest_first(tmp_path: Path) -> None:
    # Quota eviction is global across cameras: the oldest finalized wins regardless of
    # which camera it belongs to.
    cam0_old = _seg(tmp_path, 0, 40, 100)
    _seg(tmp_path, 0, 0, 100)  # cam0 active
    cam1_mid = _seg(tmp_path, 1, 20, 100)
    _seg(tmp_path, 1, 0, 100)  # cam1 active

    # 400B total, quota 350B → drop exactly the single global-oldest (cam0_old).
    evict_once(_settings(tmp_path, quota=350), now=_NOW)

    assert not cam0_old.exists()
    assert cam1_mid.exists()


def test_clips_are_never_evicted(tmp_path: Path) -> None:
    # A promoted clip lives outside the rec tree and is never scanned — heavy age + tiny
    # quota must leave it untouched.
    clips = clips_dir(str(tmp_path))
    clips.mkdir(parents=True, exist_ok=True)
    clip = clips / "event-42.mp4"
    clip.write_bytes(b"\0" * 10_000)
    _seg(tmp_path, 0, 100, 100)  # an old finalized segment to trigger eviction
    _seg(tmp_path, 0, 0, 100)  # active

    evict_once(_settings(tmp_path, quota=1, max_age_s=60), now=_NOW)

    assert clip.exists()  # clips survive by construction


def test_no_policy_pressure_is_a_noop(tmp_path: Path) -> None:
    # Under quota and no age bound → nothing is touched.
    a = _seg(tmp_path, 0, 30, 100)
    b = _seg(tmp_path, 0, 0, 100)
    evict_once(_settings(tmp_path, quota=10**12, max_age_s=0), now=_NOW)
    assert a.exists() and b.exists()
