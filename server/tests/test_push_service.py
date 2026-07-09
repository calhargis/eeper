"""Unit tests for the nudge delivery policy + copy (M2.4): quiet hours, the push
enable default, and the non-clinical copy lint (Master Plan §2)."""

from __future__ import annotations

from datetime import UTC, datetime

from eeper.api import push_service
from eeper.api.models import NotificationPreferences


def _prefs(
    *,
    push_enabled: bool = True,
    quiet_hours_enabled: bool = False,
    quiet_hours_start: int = 0,
    quiet_hours_end: int = 0,
    timezone: str = "UTC",
) -> NotificationPreferences:
    p = NotificationPreferences(user_id=1)
    p.push_enabled = push_enabled
    p.quiet_hours_enabled = quiet_hours_enabled
    p.quiet_hours_start = quiet_hours_start
    p.quiet_hours_end = quiet_hours_end
    p.timezone = timezone
    return p


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 9, hour, minute, tzinfo=UTC)


def test_should_push_defaults_on() -> None:
    assert push_service.should_push(None) is True  # no prefs row -> on by default
    assert push_service.should_push(_prefs(push_enabled=True)) is True
    assert push_service.should_push(_prefs(push_enabled=False)) is False


def test_quiet_hours_disabled_is_never_quiet() -> None:
    assert push_service.in_quiet_hours(None, _utc(3)) is False
    assert push_service.in_quiet_hours(_prefs(quiet_hours_enabled=False), _utc(3)) is False


def test_quiet_hours_same_start_end_is_empty_window() -> None:
    p = _prefs(quiet_hours_enabled=True, quiet_hours_start=120, quiet_hours_end=120)
    assert push_service.in_quiet_hours(p, _utc(2)) is False


def test_quiet_hours_daytime_window() -> None:
    # Quiet 09:00 (540) -> 17:00 (1020).
    p = _prefs(quiet_hours_enabled=True, quiet_hours_start=540, quiet_hours_end=1020)
    assert push_service.in_quiet_hours(p, _utc(12)) is True  # inside
    assert push_service.in_quiet_hours(p, _utc(8)) is False  # before
    assert push_service.in_quiet_hours(p, _utc(17)) is False  # end is exclusive


def test_quiet_hours_wraps_past_midnight() -> None:
    # The common case: quiet 22:00 (1320) -> 07:00 (420).
    p = _prefs(quiet_hours_enabled=True, quiet_hours_start=1320, quiet_hours_end=420)
    assert push_service.in_quiet_hours(p, _utc(23, 30)) is True  # late night
    assert push_service.in_quiet_hours(p, _utc(3)) is True  # early morning
    assert push_service.in_quiet_hours(p, _utc(12)) is False  # midday is loud


def test_quiet_hours_respects_timezone() -> None:
    # 03:00 UTC is 22:00 the previous day in America/New_York (UTC-5, standard time).
    p = _prefs(
        quiet_hours_enabled=True,
        quiet_hours_start=1320,
        quiet_hours_end=420,
        timezone="America/New_York",
    )
    assert push_service.in_quiet_hours(p, datetime(2026, 1, 9, 3, 0, tzinfo=UTC)) is True
    p2 = _prefs(
        quiet_hours_enabled=True,
        quiet_hours_start=1320,
        quiet_hours_end=420,
        timezone="America/New_York",
    )
    assert push_service.in_quiet_hours(p2, datetime(2026, 1, 9, 15, 0, tzinfo=UTC)) is False


def test_unknown_timezone_falls_back_to_utc() -> None:
    p = _prefs(
        quiet_hours_enabled=True, quiet_hours_start=1320, quiet_hours_end=420, timezone="Not/AZone"
    )
    # Must not raise; treated as UTC.
    assert push_service.in_quiet_hours(p, _utc(23, 30)) is True


# The Master Plan §2 stance encoded as a test: nudge copy is awareness language, never
# clinical/alarm. This mirrors the M2.4 copy-lint criterion.
_CLINICAL_DENYLIST = (
    "oxygen",
    "vital",
    "emergency",
    "apnea",
    "breathing",
    "heart",
    "pulse",
    "medical",
    "alarm",
    "alert",
    "danger",
    "distress",
    "seizure",
    "suffocat",
)


def test_nudge_copy_is_non_clinical() -> None:
    types = ["sound_elevated", "cry_detected", "movement_level_change", "unknown_type"]
    for event_type in types:
        title, body = push_service.nudge_copy(event_type)
        text = f"{title} {body}".lower()
        for term in _CLINICAL_DENYLIST:
            assert term not in text, (
                f"clinical/alarm term {term!r} in copy for {event_type}: {text!r}"
            )
        assert title and body  # non-empty
