"""M6.1 thermal features ingestion (slice 2): the ingestor validates a features message
against the §4.5 contract and enqueues it for storage; a malformed message or an
unexpected topic is dropped without raising. Pure — duck-typed MQTT messages, no broker
or DB. Also asserts the M3.1 SensorIngestor leaves the richer thermal topics alone."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from eeper.api.config import Settings
from eeper.api.ingestion import SensorIngestor
from eeper.api.thermal_ingestion import ThermalIngestor


def _settings() -> Settings:
    return Settings(database_url="postgresql+asyncpg://x/x", secret_key="x" * 16)


def _ingestor() -> ThermalIngestor:
    return ThermalIngestor(cast("Any", None), _settings())  # sessionmaker unused by _on_message


def _features_msg(
    device_id: int, *, presence: bool = True, centroid: list[float] | None = None
) -> SimpleNamespace:
    payload = json.dumps(
        {
            "ts": 1783900000.0,
            "presence": presence,
            "presence_confidence": 0.7 if presence else 0.0,
            "warm_region_area": 0.1 if presence else 0.0,
            "warm_region_centroid": centroid
            if centroid is not None
            else ([0.5, 0.5] if presence else None),
        }
    ).encode()
    return SimpleNamespace(topic=f"eeper/dev/{device_id}/thermal_features", payload=payload)


def test_valid_features_are_accepted() -> None:
    ing = _ingestor()
    ing._on_message(cast("Any", None), None, cast("Any", _features_msg(7)))
    assert ing._queue.qsize() == 1
    assert ing.stats() == {7: 1}


def test_absent_presence_with_null_centroid_is_accepted() -> None:
    ing = _ingestor()
    ing._on_message(cast("Any", None), None, cast("Any", _features_msg(7, presence=False)))
    assert ing._queue.qsize() == 1
    device_id, _ts, presence, _c, _a, row, col = ing._queue.get_nowait()
    assert device_id == 7 and presence is False and row is None and col is None


def test_malformed_message_is_dropped_without_counting() -> None:
    ing = _ingestor()
    bad = SimpleNamespace(
        topic="eeper/dev/7/thermal_features", payload=b'{"ts": 1}'
    )  # missing fields
    ing._on_message(cast("Any", None), None, cast("Any", bad))
    assert ing._queue.qsize() == 0
    assert ing.stats() == {}


def test_unexpected_topic_is_ignored() -> None:
    ing = _ingestor()
    off = SimpleNamespace(topic="eeper/insight/state", payload=_features_msg(7).payload)
    ing._on_message(cast("Any", None), None, cast("Any", off))
    assert ing._queue.qsize() == 0 and ing.stats() == {}


def test_sensor_ingestor_skips_thermal_topics() -> None:
    # The M3.1 sensor ingestor must not try to parse thermal / thermal_features as a
    # SensorMessage — those are the ThermalIngestor's richer contract.
    sensor = SensorIngestor(cast("Any", None), _settings())
    for metric in ("thermal", "thermal_features"):
        msg = SimpleNamespace(topic=f"eeper/dev/7/{metric}", payload=_features_msg(7).payload)
        sensor._on_message(cast("Any", None), None, cast("Any", msg))
    assert sensor._queue.qsize() == 0
