"""Events API, live-event WebSocket, and Web Push subscription/preferences (M2.4).

The Tonight view reads ``GET /events`` for history and holds a ``/ws/events``
WebSocket for live updates (the nudge worker broadcasts each event, and again when its
clip is ready). Only nudge-worthy event types surface here — movement-level churn stays
out of the timeline. Push subscription + per-user preferences (enable + quiet hours)
back the notification settings; the VAPID public key is served for the browser to
subscribe with.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.config import Settings, get_settings
from eeper.api.db import get_sessionmaker
from eeper.api.dependencies import CurrentUser, SessionDep, SettingsDep
from eeper.api.event_hub import event_to_out
from eeper.api.models import Event, NotificationPreferences, PushSubscription, User
from eeper.api.schemas import (
    EventOut,
    MessageOut,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
    PushSubscriptionIn,
    VapidKeyOut,
)
from eeper.api.tokens import decode_access_token

router = APIRouter(tags=["events"])

# Only nudge-worthy events surface in the Tonight view + events API (movement-level
# transitions stay out of the timeline).
_NUDGE_TYPES = ("sound_elevated", "cry_detected")


async def _query_events(
    session: AsyncSession,
    household_id: str,
    *,
    camera_id: int | None,
    since: datetime | None,
    limit: int,
) -> list[EventOut]:
    stmt = select(Event).where(Event.household_id == household_id, Event.type.in_(_NUDGE_TYPES))
    if camera_id is not None:
        stmt = stmt.where(Event.camera_id == camera_id)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        stmt = stmt.where(Event.ts >= since)
    # Tiebreak on id (the rest of the composite PK) so events sharing a ts have a
    # stable order across the since/limit window.
    stmt = stmt.order_by(Event.ts.desc(), Event.id.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [event_to_out(e) for e in rows]


@router.get("/events", response_model=list[EventOut])
async def list_events(
    user: CurrentUser,
    session: SessionDep,
    camera_id: Annotated[int | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EventOut]:
    return await _query_events(
        session, user.household_id, camera_id=camera_id, since=since, limit=limit
    )


@router.get("/cameras/{camera_id}/events", response_model=list[EventOut])
async def list_camera_events(
    camera_id: int,
    user: CurrentUser,
    session: SessionDep,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EventOut]:
    return await _query_events(
        session, user.household_id, camera_id=camera_id, since=since, limit=limit
    )


async def _authenticate_ws(
    websocket: WebSocket, session: AsyncSession, settings: Settings
) -> User | None:
    """Resolve the caller from the access cookie the browser sends on the WS upgrade
    (same JWT the HTTP auth dependency validates). Bearer tokens aren't used here — the
    Tonight view is a browser session."""
    token = websocket.cookies.get(settings.access_cookie_name)
    if not token:
        return None
    payload = decode_access_token(settings.secret_key, token)
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return await session.get(User, user_id)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        user = await _authenticate_ws(websocket, session, settings)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    hub = websocket.app.state.hub
    await websocket.accept()
    await hub.register(user.household_id, websocket)
    try:
        # The client sends nothing; receive_text blocks until the socket closes (a
        # disconnect from any cause raises, which just ends the loop and unregisters).
        with contextlib.suppress(Exception):
            while True:
                await websocket.receive_text()
    finally:
        await hub.unregister(user.household_id, websocket)


# ── Web Push subscription + preferences ────────────────────────────────────────


@router.get("/push/vapid-key", response_model=VapidKeyOut)
async def vapid_key(settings: SettingsDep) -> VapidKeyOut:
    return VapidKeyOut(public_key=settings.vapid_public_key)


@router.post(
    "/users/me/push-subscriptions", response_model=MessageOut, status_code=status.HTTP_201_CREATED
)
async def add_push_subscription(
    body: PushSubscriptionIn, user: CurrentUser, session: SessionDep
) -> MessageOut:
    existing = (
        await session.execute(
            select(PushSubscription).where(
                PushSubscription.user_id == user.id, PushSubscription.endpoint == body.endpoint
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.p256dh, existing.auth = body.keys.p256dh, body.keys.auth
    else:
        session.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
            )
        )
    await session.commit()
    return MessageOut(detail="Subscribed to notifications")


@router.delete("/users/me/push-subscriptions", response_model=MessageOut)
async def remove_push_subscription(
    user: CurrentUser, session: SessionDep, endpoint: Annotated[str, Query(min_length=1)]
) -> MessageOut:
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == endpoint
        )
    )
    await session.commit()
    return MessageOut(detail="Unsubscribed")


def _prefs_out(prefs: NotificationPreferences | None) -> NotificationPreferencesOut:
    if prefs is None:  # defaults for a user who has never set preferences
        return NotificationPreferencesOut(
            push_enabled=True,
            quiet_hours_enabled=False,
            quiet_hours_start=0,
            quiet_hours_end=0,
            timezone="UTC",
        )
    return NotificationPreferencesOut(
        push_enabled=prefs.push_enabled,
        quiet_hours_enabled=prefs.quiet_hours_enabled,
        quiet_hours_start=prefs.quiet_hours_start,
        quiet_hours_end=prefs.quiet_hours_end,
        timezone=prefs.timezone,
    )


@router.get("/users/me/notification-preferences", response_model=NotificationPreferencesOut)
async def get_notification_preferences(
    user: CurrentUser, session: SessionDep
) -> NotificationPreferencesOut:
    prefs = (
        await session.execute(
            select(NotificationPreferences).where(NotificationPreferences.user_id == user.id)
        )
    ).scalar_one_or_none()
    return _prefs_out(prefs)


@router.patch("/users/me/notification-preferences", response_model=NotificationPreferencesOut)
async def update_notification_preferences(
    body: NotificationPreferencesIn, user: CurrentUser, session: SessionDep
) -> NotificationPreferencesOut:
    prefs = (
        await session.execute(
            select(NotificationPreferences).where(NotificationPreferences.user_id == user.id)
        )
    ).scalar_one_or_none()
    if prefs is None:
        prefs = NotificationPreferences(user_id=user.id)
        session.add(prefs)
    if body.push_enabled is not None:
        prefs.push_enabled = body.push_enabled
    if body.quiet_hours_enabled is not None:
        prefs.quiet_hours_enabled = body.quiet_hours_enabled
    if body.quiet_hours_start is not None:
        prefs.quiet_hours_start = body.quiet_hours_start
    if body.quiet_hours_end is not None:
        prefs.quiet_hours_end = body.quiet_hours_end
    if body.timezone is not None:
        prefs.timezone = body.timezone
    await session.commit()
    await session.refresh(prefs)
    return _prefs_out(prefs)
