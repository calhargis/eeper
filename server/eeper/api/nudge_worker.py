"""api-side nudge worker (M2.4): the DB-as-queue consumer.

The insight engine writes a nudge-worthy event with each delivery channel ``pending``;
this worker does the side effects and marks them, so the events table *is* the queue.
That buys crash recovery for free — a worker that dies mid-delivery restarts, scans for
``pending`` rows, and resumes with nothing lost.

Wake-up is two-layer: Postgres ``LISTEN/NOTIFY`` (instant, and transactional — the
notify is delivered only when the insert commits, so it can never race ahead of the row
or fire for a rollback) plus a slow reconciliation poll as the never-lost safety net
(NOTIFY has no delivery guarantee across a dropped connection).

Three idempotent channels run per event: **broadcast** (push the event to Tonight-view
WebSocket clients — immediate), **push** (Web Push under the per-user enable +
quiet-hours policy and a per-camera rate limit — immediate), and **clip** (auto-promote
a pre/post-roll clip once the post-roll has actually recorded — delayed). Delivery
POLICY lives here, never in the insight detector.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eeper.api import push_service
from eeper.api.clip_service import ClipPromotionError, promote_clip_for_window
from eeper.api.config import Settings
from eeper.api.event_hub import EventHub, event_message
from eeper.api.models import Event, NotificationPreferences, PushSubscription, User

_log = logging.getLogger("eeper.api.nudge_worker")
_NOTIFY_CHANNEL = "eeper_new_event"
_MAX_CLIP_ATTEMPTS = 3
_BATCH = 200


class NudgeWorker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        hub: EventHub,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._hub = hub
        # asyncpg wants a plain postgresql:// DSN, not SQLAlchemy's +asyncpg dialect.
        self._dsn = settings.database_url.replace("+asyncpg", "")
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        self._wake.set()  # scan once on boot — deliver anything left pending by a prior run
        self._tasks = [
            asyncio.create_task(self._process_loop()),
            asyncio.create_task(self._listen_loop()),
        ]

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    # ── wake-up layers ──────────────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Hold a LISTEN connection; a NOTIFY wakes the process loop. Reconnect on
        drop — the reconciliation poll covers the gap losslessly."""
        while not self._stopping:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self._dsn)
                await conn.add_listener(_NOTIFY_CHANNEL, lambda *_: self._wake.set())
                self._wake.set()  # (re)connect: scan for anything missed while down
                while not self._stopping:
                    # Hold the connection (the callback does the work), but poll its
                    # health so a dropped socket is noticed in seconds, not an hour.
                    await asyncio.sleep(5)
                    if conn.is_closed():
                        raise ConnectionError("LISTEN connection closed")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a listener drop is a latency hit, not a loss
                _log.warning("event listener dropped; reconnecting", exc_info=True)
                await asyncio.sleep(2)
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()

    async def _process_loop(self) -> None:
        while not self._stopping:
            try:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self._settings.nudge_reconcile_interval_seconds
                    )
                self._wake.clear()
                await self._process_pending()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never let a bad pass kill the loop
                _log.exception("nudge process pass failed")
                await asyncio.sleep(1)

    # ── delivery ────────────────────────────────────────────────────────────────

    async def _process_pending(self) -> None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Event.id)
                .where(
                    (Event.broadcast_status == "pending")
                    | (Event.clip_status == "pending")
                    | (Event.nudge_status == "pending")
                )
                .order_by(Event.ts)
                .limit(_BATCH)
            )
            ids = [row[0] for row in result.all()]
        for event_id in ids:
            with contextlib.suppress(Exception):  # one event's failure never stalls the queue
                await self._deliver(event_id)

    async def _get(self, session: AsyncSession, event_id: int) -> Event | None:
        return (
            await session.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()

    async def _deliver(self, event_id: int) -> None:
        """Run each pending channel in its own transaction (so partial progress
        persists and one channel's failure doesn't block the others). Every channel is
        idempotent — re-running a delivered channel is a no-op — so a crash anywhere
        resumes losslessly on the next pass."""
        now = datetime.now(UTC)

        # 1. broadcast — immediate.
        async with self._sessionmaker() as s:
            ev = await self._get(s, event_id)
            if ev is not None and ev.broadcast_status == "pending":
                await self._hub.broadcast(ev.household_id, event_message(ev))
                ev.broadcast_status = "sent"
                await s.commit()

        # 2. push — immediate, under policy. A transient send failure is retried like
        # the clip channel (kept pending up to _MAX_CLIP_ATTEMPTS); 'sent'/'suppressed'
        # are terminal.
        async with self._sessionmaker() as s:
            ev = await self._get(s, event_id)
            if ev is not None and ev.nudge_status == "pending":
                outcome = await self._push_for_event(s, ev, now)
                if outcome == "failed":
                    ev.delivery_attempts += 1
                    ev.nudge_status = (
                        "failed" if ev.delivery_attempts >= _MAX_CLIP_ATTEMPTS else "pending"
                    )
                else:
                    ev.nudge_status = outcome  # sent | suppressed (terminal)
                await s.commit()

        # 3. clip — only once the post-roll has actually recorded.
        async with self._sessionmaker() as s:
            ev = await self._get(s, event_id)
            ready = (
                now >= ev.ts + timedelta(seconds=self._settings.nudge_post_roll_seconds)
                if ev
                else False
            )
            if ev is not None and ev.clip_status == "pending" and ready:
                await self._promote_clip(s, ev)

        # 4. mark delivered when every channel is terminal.
        async with self._sessionmaker() as s:
            ev = await self._get(s, event_id)
            if (
                ev is not None
                and ev.delivered_at is None
                and "pending"
                not in (
                    ev.broadcast_status,
                    ev.clip_status,
                    ev.nudge_status,
                )
            ):
                ev.delivered_at = datetime.now(UTC)
                await s.commit()

    async def _promote_clip(self, session: AsyncSession, ev: Event) -> None:
        pre = timedelta(seconds=self._settings.nudge_pre_roll_seconds)
        post = timedelta(seconds=self._settings.nudge_post_roll_seconds)
        try:
            clip = await promote_clip_for_window(
                session=session,
                media_root=self._settings.media_root,
                clip_max_seconds=self._settings.clip_max_seconds,
                household_id=ev.household_id,
                camera_id=ev.camera_id,
                start=ev.ts - pre,
                end=ev.ts + post,
            )
            ev.clip_id = clip.id
            ev.clip_status = "promoted"
            await session.commit()  # clip row + event link commit together (atomic)
            fresh = await self._get(session, ev.id)  # re-broadcast now the clip is attached
            if fresh is not None:
                await self._hub.broadcast(fresh.household_id, event_message(fresh))
        except ClipPromotionError as exc:
            await session.rollback()
            refetched = await self._get(session, ev.id)
            if refetched is None:
                return
            refetched.delivery_attempts += 1
            refetched.clip_status = (
                "failed" if refetched.delivery_attempts >= _MAX_CLIP_ATTEMPTS else "pending"
            )
            await session.commit()
            _log.info(
                "clip auto-promotion for event %s: %s (attempt %s)",
                refetched.id,
                exc.code,
                refetched.delivery_attempts,
            )

    async def _push_for_event(self, session: AsyncSession, ev: Event, now: datetime) -> str:
        """Send Web Push to every eligible household member. Returns: 'sent' (>=1 push
        delivered), 'failed' (eligible subscriptions existed but every send hit a
        transient error — retry), or 'suppressed' (push disabled, rate-limited, or no
        eligible/subscribed user — terminal)."""
        if not self._settings.vapid_private_key:
            return "suppressed"  # push disabled — clip + broadcast still ran
        # Per-camera rate limit, anchored on EVENT time (not delivery time): a backlog or
        # crash-recovery replay must still see a prior nudge's event, so the window is
        # [ev.ts - min_interval, ev.ts). (Anchoring on `now` would empty the window
        # whenever delivery lag exceeds the interval, defeating the limit.)
        window = ev.ts - timedelta(seconds=self._settings.nudge_min_interval_seconds)
        recent = (
            await session.execute(
                select(Event.id)
                .where(
                    Event.camera_id == ev.camera_id,
                    Event.nudge_status == "sent",
                    Event.ts >= window,
                    Event.ts < ev.ts,
                )
                .limit(1)
            )
        ).first()
        if recent is not None:
            return "suppressed"

        users = (
            (await session.execute(select(User).where(User.household_id == ev.household_id)))
            .scalars()
            .all()
        )
        title, body = push_service.nudge_copy(ev.type)
        payload: dict[str, object] = {
            "event_id": ev.id,
            "camera_id": ev.camera_id,
            "type": ev.type,
            "clip_id": ev.clip_id,
            "title": title,
            "body": body,
            "url": "/tonight",
        }
        topic = f"e{ev.id}"  # collapse key: a retried nudge never double-notifies
        sent_any = attempted = any_failed = False
        for user in users:
            prefs = (
                await session.execute(
                    select(NotificationPreferences).where(
                        NotificationPreferences.user_id == user.id
                    )
                )
            ).scalar_one_or_none()
            if not push_service.should_push(prefs) or push_service.in_quiet_hours(prefs, now):
                continue
            subs = (
                (
                    await session.execute(
                        select(PushSubscription).where(PushSubscription.user_id == user.id)
                    )
                )
                .scalars()
                .all()
            )
            for sub in subs:
                attempted = True
                result = await push_service.send_push(
                    sub,
                    payload,
                    vapid_private_key=self._settings.vapid_private_key,
                    vapid_subject=self._settings.vapid_subject,
                    topic=topic,
                )
                if result == "sent":
                    sent_any = True
                elif result == "gone":
                    await session.delete(sub)
                else:  # transient failure
                    any_failed = True
        if sent_any:
            return "sent"
        if attempted and any_failed:
            return "failed"  # real subscriptions, transient errors — retry next pass
        return "suppressed"  # no eligible/subscribed user, or only dead subscriptions
