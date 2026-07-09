"""Web Push sending, nudge copy, and quiet-hours policy (M2.4).

The nudge worker is the one place delivery policy lives: it decides per-user whether a
push goes out (master toggle + quiet hours) and renders the copy. The copy is
deliberately non-clinical (Master Plan §2) — eeper reports awareness (sound, activity),
never a medical or alarm reading — and a copy-lint test asserts it against a
clinical/alarm denylist.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pywebpush import WebPushException, webpush

from eeper.api.models import NotificationPreferences, PushSubscription

_log = logging.getLogger("eeper.api.push_service")

# (title, body) per nudge event type. Sound-level is the v1 nudge; cry is experimental.
_COPY: dict[str, tuple[str, str]] = {
    "sound_elevated": (
        "Sound in the nursery",
        "eeper heard sustained sound. Tap to see the moment.",
    ),
    "cry_detected": ("Possible crying", "eeper may have heard crying. Tap to see the moment."),
}
_DEFAULT_COPY = ("Nursery activity", "eeper noticed something. Tap to take a look.")


def nudge_copy(event_type: str) -> tuple[str, str]:
    return _COPY.get(event_type, _DEFAULT_COPY)


def should_push(prefs: NotificationPreferences | None) -> bool:
    """Push is on by default (no prefs row yet) unless the user turned it off."""
    return prefs is None or prefs.push_enabled


def in_quiet_hours(prefs: NotificationPreferences | None, now_utc: datetime) -> bool:
    """Whether ``now`` falls inside the user's quiet-hours window (minutes-of-day in
    their timezone), handling a window that wraps past midnight (22:00 -> 07:00)."""
    if prefs is None or not prefs.quiet_hours_enabled:
        return False
    start, end = prefs.quiet_hours_start, prefs.quiet_hours_end
    if start == end:
        return False  # empty window
    try:
        tz = ZoneInfo(prefs.timezone)
    except Exception:  # noqa: BLE001 — an unknown tz string falls back to UTC, never raises
        tz = ZoneInfo("UTC")
    local = now_utc.astimezone(tz)
    minute = local.hour * 60 + local.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps past midnight


async def send_push(
    sub: PushSubscription,
    payload: dict[str, object],
    *,
    vapid_private_key: str,
    vapid_subject: str,
    topic: str | None = None,
) -> str:
    """Send one VAPID Web Push. Returns ``"sent"`` | ``"gone"`` (the push service says
    the subscription is dead — 404/410, delete it) | ``"failed"`` (transient — retry).
    ``topic`` is a collapse key: the push service replaces an undelivered message with
    the same topic, and the service worker keys its notification on the event id too, so
    a retried nudge never shows the parent two notifications. pywebpush is synchronous
    (requests), so it runs in a worker thread."""

    def _send() -> str:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_subject},
                timeout=10,
                headers={"Topic": topic} if topic else None,
            )
            return "sent"
        except WebPushException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (404, 410):
                return "gone"
            _log.warning("web push to %s failed (%s)", sub.endpoint[:40], code)
            return "failed"

    return await asyncio.to_thread(_send)
