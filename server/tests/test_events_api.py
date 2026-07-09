"""Events API + notification-preferences + push-subscription endpoints (M2.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eeper.api.models import Event
from tests.conftest import Harness

ADMIN_USER, ADMIN_PW = "admin", "correct horse battery staple"


async def _first_boot(api: Harness) -> None:
    r = await api.client.post(
        "/api/v1/system/first-boot", json={"username": ADMIN_USER, "password": ADMIN_PW}
    )
    assert r.status_code in (200, 201), r.text


async def _seed_events(api: Harness) -> None:
    engine = create_async_engine(api.settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessionmaker() as s:
        s.add(
            Event(
                ts=now,
                camera_id=1,
                type="sound_elevated",
                value="elevated",
                confidence=0.9,
                clip_id=77,
            )
        )
        s.add(
            Event(
                ts=now - timedelta(minutes=1),
                camera_id=2,
                type="cry_detected",
                value="crying",
                confidence=0.8,
            )
        )
        # A movement-level change is NOT a nudge and must be excluded from the timeline.
        s.add(
            Event(ts=now, camera_id=1, type="movement_level_change", value="high", confidence=0.5)
        )
        await s.commit()
    await engine.dispose()


async def test_events_lists_only_nudges_with_clip_ref(api: Harness) -> None:
    await _first_boot(api)
    await _seed_events(api)
    r = await api.client.get("/api/v1/events")
    assert r.status_code == 200, r.text
    events = r.json()
    assert {e["type"] for e in events} == {"sound_elevated", "cry_detected"}  # movement excluded
    sound = next(e for e in events if e["type"] == "sound_elevated")
    assert sound["clip_id"] == 77
    # Per-camera filter.
    r2 = await api.client.get("/api/v1/cameras/2/events")
    assert [e["type"] for e in r2.json()] == ["cry_detected"]


async def test_events_require_auth(api: Harness) -> None:
    async with api.fresh() as anon:
        assert (await anon.get("/api/v1/events")).status_code == 401


async def test_notification_preferences_get_and_update(api: Harness) -> None:
    await _first_boot(api)
    # Defaults for a user who has never set preferences.
    r = await api.client.get("/api/v1/users/me/notification-preferences")
    assert r.status_code == 200
    assert r.json()["push_enabled"] is True
    assert r.json()["quiet_hours_enabled"] is False
    # Partial update (quiet hours 22:00 -> 07:00).
    r = await api.client.patch(
        "/api/v1/users/me/notification-preferences",
        json={"quiet_hours_enabled": True, "quiet_hours_start": 1320, "quiet_hours_end": 420},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["quiet_hours_enabled"] is True
    assert body["quiet_hours_start"] == 1320
    assert body["push_enabled"] is True  # untouched fields preserved
    # Persisted.
    r = await api.client.get("/api/v1/users/me/notification-preferences")
    assert r.json()["quiet_hours_start"] == 1320
    # Out-of-range minutes are rejected.
    bad = await api.client.patch(
        "/api/v1/users/me/notification-preferences", json={"quiet_hours_start": 5000}
    )
    assert bad.status_code == 422


async def test_push_subscription_lifecycle(api: Harness) -> None:
    await _first_boot(api)
    r = await api.client.get("/api/v1/push/vapid-key")
    assert r.status_code == 200
    assert "public_key" in r.json()  # empty in the test settings, but served
    endpoint = "https://push.example/abc"
    sub = {"endpoint": endpoint, "keys": {"p256dh": "pkey", "auth": "akey"}}
    r = await api.client.post("/api/v1/users/me/push-subscriptions", json=sub)
    assert r.status_code == 201, r.text
    # Idempotent re-subscribe (same endpoint) updates keys, no duplicate.
    r = await api.client.post("/api/v1/users/me/push-subscriptions", json=sub)
    assert r.status_code == 201
    r = await api.client.delete(
        "/api/v1/users/me/push-subscriptions", params={"endpoint": endpoint}
    )
    assert r.status_code == 200
