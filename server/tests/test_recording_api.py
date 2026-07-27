"""Recording settings — the admin toggle the recorder reads.

The critical invariant is the MISSING-ROW DEFAULT: recording must be ON when no row exists,
so upgrading an existing deployment keeps recording exactly as before and a fresh install
records out of the box. A default of off would silently stop clips for everyone on upgrade.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import Harness

ADMIN = {"username": "recadmin", "password": "correct horse battery staple"}


async def _sign_in_admin(api: Harness) -> None:
    r = await api.client.post("/api/v1/system/first-boot", json=ADMIN)
    assert r.status_code == 201, r.text


async def test_defaults_to_enabled_with_no_row(api: Harness) -> None:
    await _sign_in_admin(api)
    r = await api.client.get("/api/v1/recording/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recording_enabled"] is True, "a missing row must mean recording is ON"
    assert body["storage_target_id"] == "internal"


async def test_admin_can_toggle_and_it_persists(api: Harness) -> None:
    await _sign_in_admin(api)
    r = await api.client.patch("/api/v1/recording/settings", json={"recording_enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["recording_enabled"] is False

    # Survives a re-read (it is the recorder's source of truth, not UI state).
    assert (await api.client.get("/api/v1/recording/settings")).json()["recording_enabled"] is False

    # And back on again — the toggle is not one-way.
    r = await api.client.patch("/api/v1/recording/settings", json={"recording_enabled": True})
    assert r.json()["recording_enabled"] is True


async def test_patch_is_partial_and_does_not_reset_other_fields(
    api: Harness, tmp_path: Path
) -> None:
    api.settings.storage_targets = f"ssd:External SSD:{tmp_path}"
    await _sign_in_admin(api)
    await api.client.patch("/api/v1/recording/settings", json={"storage_target_id": "ssd"})
    # Toggling recording must leave the storage choice alone.
    body = (
        await api.client.patch("/api/v1/recording/settings", json={"recording_enabled": False})
    ).json()
    assert body["storage_target_id"] == "ssd", "a partial PATCH must not reset untouched fields"
    assert body["recording_enabled"] is False


async def test_rejects_a_path_like_storage_target(api: Harness) -> None:
    await _sign_in_admin(api)
    # The id names an allow-listed target; it must never be usable to smuggle in a path.
    for bad in ["../../etc", "/mnt/ssd", "has space", "A" * 40]:
        r = await api.client.patch("/api/v1/recording/settings", json={"storage_target_id": bad})
        assert r.status_code == 422, f"{bad!r} should be rejected, got {r.status_code}"


async def test_non_admin_cannot_change_settings(api: Harness) -> None:
    await _sign_in_admin(api)
    r = await api.client.post(
        "/api/v1/users", json={"username": "viewer1", "password": "x" * 12, "role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text

    async with api.fresh() as viewer:
        r = await viewer.post(
            "/api/v1/auth/login", json={"username": "viewer1", "password": "x" * 12}
        )
        assert r.status_code == 200, r.text
        # A viewer may READ (so the UI can explain why clips are or aren't captured)...
        assert (await viewer.get("/api/v1/recording/settings")).status_code == 200
        # ...but must not be able to turn recording off.
        r = await viewer.patch("/api/v1/recording/settings", json={"recording_enabled": False})
        assert r.status_code == 403, f"a viewer must not change recording settings: {r.text}"


async def test_clip_intent_is_withdrawn_when_recording_is_off(api: Harness) -> None:
    """The invariant that keeps Tonight honest: with recording off, a nudge-worthy event
    must NOT be marked clip-pending, because no segment will ever be written for it. Push
    and broadcast still fire — the parent is told, there just isn't a clip to attach."""
    from eeper.api.recording_settings import is_recording_enabled

    await _sign_in_admin(api)
    await api.client.patch("/api/v1/recording/settings", json={"recording_enabled": False})

    # The gate the insight writer and nudge worker both consult.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from eeper.api.db import get_session  # noqa: F401 — session comes from the harness

    engine = create_async_engine(api.settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        assert await is_recording_enabled(s) is False, "the recorder gate must read False"
    await engine.dispose()


async def test_failed_promotion_records_its_attempt(api: Harness) -> None:
    """A clip promotion that fails must COUNT the attempt and eventually mark the event
    'failed'. It previously did neither: the except-block called session.rollback(), which
    expires the ORM instance, then touched ev.id — triggering a lazy refresh that raises
    MissingGreenlet from inside the handler. The bookkeeping was skipped and rows sat
    'pending' with delivery_attempts=0 forever (193 of them on a real deployment)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from eeper.api.event_hub import EventHub
    from eeper.api.models import Event
    from eeper.api.nudge_worker import NudgeWorker

    engine = create_async_engine(api.settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            ev = Event(
                ts=datetime.now(UTC) - timedelta(minutes=5),  # post-roll long elapsed
                camera_id=1,
                type="sound_elevated",
                value="elevated",
                confidence=0.9,
                clip_status="pending",
                nudge_status="skip",
                broadcast_status="skip",
            )
            s.add(ev)
            await s.commit()
            event_id = ev.id

        worker = NudgeWorker(sm, api.settings, EventHub())
        async with sm() as s:
            fresh = (await s.execute(select(Event).where(Event.id == event_id))).scalar_one()
            # No segments exist, so promotion raises ClipPromotionError. The handler must
            # survive its own rollback and record the attempt.
            await worker._promote_clip(s, fresh)

        async with sm() as s:
            after = (await s.execute(select(Event).where(Event.id == event_id))).scalar_one()
        assert after.delivery_attempts == 1, "a failed promotion must count its attempt"
        assert after.clip_status == "pending", "still retryable below the attempt cap"
    finally:
        await engine.dispose()
