"""Pulse-ox quality-gate tests (M4.2 slice 2): the ingestor discards low-confidence
samples (never enqueued for storage) and counts them so the discard rate is observable.
Pure — a duck-typed MQTT message, no broker or DB.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from eeper.api.config import Settings
from eeper.api.pulseox_ingestion import PulseOxIngestor


def _ingestor(threshold: float = 0.5) -> PulseOxIngestor:
    settings = Settings(
        database_url="postgresql+asyncpg://x/x",
        secret_key="x" * 16,
        pulseox_profile_enabled=True,
        pulseox_quality_threshold=threshold,
    )
    return PulseOxIngestor(cast("Any", None), settings)  # sessionmaker unused by _on_message


def _msg(device_id: int, quality: float, hr: float = 120.0) -> SimpleNamespace:
    payload = json.dumps(
        {"ts": 1783900000.0, "hr": hr, "spo2": 98.0, "perfusion": 4.0, "quality": quality}
    ).encode()
    return SimpleNamespace(topic=f"eeper/dev/{device_id}/pulseox", payload=payload)


def test_high_quality_sample_is_accepted() -> None:
    ing = _ingestor()
    ing._on_message(cast("Any", None), None, cast("Any", _msg(7, quality=0.9)))
    assert ing._queue.qsize() == 1
    assert ing.stats() == {7: (1, 0)}  # (accepted, discarded)


def test_low_quality_sample_is_discarded_not_enqueued() -> None:
    ing = _ingestor(threshold=0.5)
    ing._on_message(cast("Any", None), None, cast("Any", _msg(7, quality=0.2)))
    assert ing._queue.qsize() == 0  # never queued for storage
    assert ing.stats() == {7: (0, 1)}  # counted as discarded


def test_discard_rate_is_observable() -> None:
    ing = _ingestor(threshold=0.5)
    for q in (0.9, 0.8, 0.1, 0.4):  # two accepted, two discarded
        ing._on_message(cast("Any", None), None, cast("Any", _msg(7, quality=q)))
    accepted, discarded = ing.stats()[7]
    assert (accepted, discarded) == (2, 2)
    assert discarded / (accepted + discarded) == 0.5


def test_malformed_sample_is_dropped_without_counting() -> None:
    ing = _ingestor()
    bad = SimpleNamespace(
        topic="eeper/dev/7/pulseox", payload=b'{"ts": 1, "hr": 120}'
    )  # missing fields
    ing._on_message(cast("Any", None), None, cast("Any", bad))
    assert ing._queue.qsize() == 0
    assert ing.stats() == {}  # a validation drop is neither accepted nor a quality discard


def test_unexpected_topic_is_ignored() -> None:
    ing = _ingestor()
    off = SimpleNamespace(topic="eeper/insight/state", payload=_msg(7, 0.9).payload)
    ing._on_message(cast("Any", None), None, cast("Any", off))
    assert ing._queue.qsize() == 0 and ing.stats() == {}
