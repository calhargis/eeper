"""A tiny, dependency-free health/version helper.

Real services import this to report a consistent liveness payload. It also gives
the Phase 0 lint/type-check/test pipeline something meaningful to exercise.
"""

from __future__ import annotations

from typing import Literal, TypedDict

Status = Literal["ok", "degraded"]


class HealthReport(TypedDict):
    """Shape of the payload every service returns from its health endpoint."""

    status: Status
    version: str


def health_report(*, degraded: bool = False) -> HealthReport:
    """Build a health payload.

    Args:
        degraded: When True, report ``"degraded"`` instead of ``"ok"``.

    Returns:
        A :class:`HealthReport` with the current package version.
    """
    from eeper import __version__

    return {"status": "degraded" if degraded else "ok", "version": __version__}
