"""Unit tests for the health helper — also proves the pytest wiring works."""

from __future__ import annotations

from eeper import __version__
from eeper.health import health_report


def test_health_report_ok() -> None:
    report = health_report()
    assert report["status"] == "ok"
    assert report["version"] == __version__


def test_health_report_degraded() -> None:
    assert health_report(degraded=True)["status"] == "degraded"
