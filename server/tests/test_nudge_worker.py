"""Nudge-worker tests (M2.4) against a real Postgres — the DB-as-queue crash-safety
properties: reconciliation delivers without NOTIFY, delivery is exactly-once across a
restart, a rolled-back insert produces no side effects, and the per-user policy
(enable + quiet hours) and per-camera rate limit gate push. The push send itself is
faked (a recorder) — the real VAPID/HTTP path is covered by test_push_send.py; here we
test the queue + policy logic deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from eeper.api import push_service
from eeper.api.config import Settings
from eeper.api.db import _EVENT_COLUMN_MIGRATION, _EVENT_NOTIFY_SQL
from eeper.api.event_hub import EventHub
from eeper.api.models import Base, Event, NotificationPreferences, PushSubscription, User
from eeper.api.nudge_worker import NudgeWorker


class RecordingHub(EventHub):
    """An EventHub that records broadcasts instead of touching real sockets."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def broadcast(self, household_id: str, message: dict[str, Any]) -> None:
        self.messages.append((household_id, message))


@dataclass
class Env:
    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    hub: RecordingHub
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def worker(self) -> NudgeWorker:
        return NudgeWorker(self.sessionmaker, self.settings, self.hub)


@pytest_asyncio.fixture
async def env(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _EVENT_NOTIFY_SQL:  # the pg_notify trigger (not part of create_all)
            await conn.exec_driver_sql(stmt)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=postgres_url,
        secret_key="x" * 16,
        vapid_private_key="test-private-key",  # non-empty => the push path runs
        vapid_subject="mailto:test@eeper.local",
        nudge_post_roll_seconds=3600,  # keep the clip channel from firing (no media here)
        nudge_reconcile_interval_seconds=0.2,
        nudge_min_interval_seconds=60,
    )
    env = Env(sessionmaker=sessionmaker, settings=settings, hub=RecordingHub())

    async def fake_send(sub: PushSubscription, payload: dict[str, Any], **_: Any) -> str:
        env.sent.append((sub.endpoint, dict(payload)))
        return "sent"

    monkeypatch.setattr(push_service, "send_push", fake_send)
    yield env
    await engine.dispose()


async def _add_user(
    env: Env, endpoint: str | None = "https://push.example/x", **pref_kw: Any
) -> User:
    async with env.sessionmaker() as s:
        user = User(
            household_id="default", username=f"u{endpoint}", password_hash="x", role="admin"
        )
        s.add(user)
        await s.flush()
        if pref_kw:
            s.add(NotificationPreferences(user_id=user.id, **pref_kw))
        if endpoint is not None:
            s.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh="p", auth="a"))
        await s.commit()
        return user


async def _insert_event(env: Env, camera_id: int = 1, ts: datetime | None = None) -> int:
    async with env.sessionmaker() as s:
        ev = Event(
            ts=ts or datetime.now(UTC),
            camera_id=camera_id,
            type="sound_elevated",
            value="elevated",
            confidence=0.9,
            clip_status="pending",
            nudge_status="pending",
            broadcast_status="pending",
        )
        s.add(ev)
        await s.commit()
        return ev.id


async def _event(env: Env, event_id: int) -> Event:
    async with env.sessionmaker() as s:
        return (await s.execute(select(Event).where(Event.id == event_id))).scalar_one()


async def test_reconciliation_delivers_without_notify(env: Env) -> None:
    # A single process pass (the reconciliation poll, no LISTEN involved) delivers a
    # pending event: broadcast to WS clients and push to the subscribed user.
    await _add_user(env)
    event_id = await _insert_event(env)
    await env.worker()._process_pending()
    ev = await _event(env, event_id)
    assert ev.broadcast_status == "sent"
    assert ev.nudge_status == "sent"
    assert len(env.sent) == 1
    assert len(env.hub.messages) == 1


async def test_delivery_is_exactly_once_across_restart(env: Env) -> None:
    # Crash recovery + idempotency: after delivery, a second worker (a restart) must
    # not re-broadcast or re-push — the event's channels are already terminal.
    await _add_user(env)
    await _insert_event(env)
    await env.worker()._process_pending()  # worker A delivers
    assert len(env.sent) == 1
    await env.worker()._process_pending()  # worker B (restart) sees nothing pending
    assert len(env.sent) == 1
    assert len(env.hub.messages) == 1


async def test_rolled_back_event_has_no_side_effects(env: Env) -> None:
    # A transactional NOTIFY only fires on commit; a rolled-back insert leaves no row
    # and the queue delivers nothing.
    await _add_user(env)
    async with env.sessionmaker() as s:
        s.add(
            Event(
                ts=datetime.now(UTC),
                camera_id=1,
                type="sound_elevated",
                value="elevated",
                confidence=0.9,
                clip_status="pending",
                nudge_status="pending",
                broadcast_status="pending",
            )
        )
        await s.flush()
        await s.rollback()
    await env.worker()._process_pending()
    assert env.sent == []
    assert env.hub.messages == []
    async with env.sessionmaker() as s:
        count = (await s.execute(select(func.count()).select_from(Event))).scalar_one()
    assert count == 0


async def test_push_policy_matrix(env: Env) -> None:
    # subscribed + on -> pushed; push disabled -> not; quiet hours now -> not; no
    # subscription -> not. Exactly one push goes out (to the eligible user).
    await _add_user(env, endpoint="https://push/on", push_enabled=True)
    await _add_user(env, endpoint="https://push/off", push_enabled=False)
    now_min = datetime.now(UTC).hour * 60 + datetime.now(UTC).minute
    await _add_user(
        env,
        endpoint="https://push/quiet",
        quiet_hours_enabled=True,
        quiet_hours_start=(now_min - 30) % 1440,
        quiet_hours_end=(now_min + 30) % 1440,
    )
    await _add_user(env, endpoint=None)  # eligible but no subscription
    await _insert_event(env)
    ev_id = await _insert_event(env, ts=datetime.now(UTC) + timedelta(milliseconds=1))
    await env.worker()._process_pending()
    endpoints = {e for e, _ in env.sent}
    assert endpoints == {"https://push/on"}
    ev = await _event(env, ev_id)
    assert ev.nudge_status in ("sent", "suppressed")


async def test_rate_limit_suppresses_a_rapid_second_nudge(env: Env) -> None:
    await _add_user(env)
    base = datetime.now(UTC)
    first = await _insert_event(env, camera_id=5, ts=base)
    second = await _insert_event(env, camera_id=5, ts=base + timedelta(seconds=5))  # within 60s
    await env.worker()._process_pending()
    assert (await _event(env, first)).nudge_status == "sent"
    assert (await _event(env, second)).nudge_status == "suppressed"
    assert len(env.sent) == 1  # only the first pushed


async def test_full_worker_notify_path_delivers(env: Env) -> None:
    # End-to-end through the running worker: LISTEN/NOTIFY (or the startup scan) drives
    # delivery without an explicit _process_pending call.
    await _add_user(env)
    worker = env.worker()
    await worker.start()
    try:
        await _insert_event(env)  # the trigger fires pg_notify on commit
        for _ in range(50):  # up to ~5s
            if env.sent:
                break
            await asyncio.sleep(0.1)
    finally:
        await worker.stop()
    assert len(env.sent) == 1


async def test_transient_push_failure_is_retried_then_terminal(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A transient send failure keeps the event pending (retryable), unlike a policy
    # suppression; after the attempt cap it goes terminal 'failed'.
    await _add_user(env)
    event_id = await _insert_event(env)

    async def failing_send(*_: object, **__: object) -> str:
        return "failed"

    monkeypatch.setattr(push_service, "send_push", failing_send)
    worker = env.worker()
    for _ in range(2):
        await worker._process_pending()
        assert (await _event(env, event_id)).nudge_status == "pending"  # still retrying
    await worker._process_pending()  # 3rd attempt -> terminal
    assert (await _event(env, event_id)).nudge_status == "failed"


async def test_upgrade_migration_adds_columns_then_trigger_applies(postgres_url: str) -> None:
    # The critical upgrade path: an events table predating the delivery columns must gain
    # them (ADD COLUMN IF NOT EXISTS) so the notify trigger, which references them, can be
    # created without aborting boot.
    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("DROP TABLE IF EXISTS events CASCADE")
            await conn.exec_driver_sql(
                "CREATE TABLE events (ts timestamptz NOT NULL, "
                "id bigint GENERATED ALWAYS AS IDENTITY, camera_id bigint, "
                "type varchar(48), value varchar(16), confidence double precision, "
                "PRIMARY KEY (ts, id))"  # an M2.2-era table: no delivery columns
            )
            # The M2.4 forward migration + trigger must apply cleanly (this is exactly
            # what aborted boot before the fix).
            await conn.exec_driver_sql(_EVENT_COLUMN_MIGRATION)
            for stmt in _EVENT_NOTIFY_SQL:
                await conn.exec_driver_sql(stmt)
            rows = (
                await conn.exec_driver_sql(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='events'"
                )
            ).fetchall()
        columns = {r[0] for r in rows}
        assert {"clip_status", "nudge_status", "broadcast_status", "delivery_attempts"} <= columns
    finally:
        await engine.dispose()
