"""A single source of 'now', injected as a FastAPI dependency.

Making the clock a dependency lets tests advance time deterministically (via
``app.dependency_overrides``) to exercise the brute-force lockout window without
real sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime


def get_now() -> datetime:
    return datetime.now(UTC)
