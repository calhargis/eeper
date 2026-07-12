"""Unit tests for the sensor contract + ingestion drop logic (M3.1).

The contract validation and the paho callback's malformed/oversized handling are pure
and broker-free; the full pair -> publish -> sensor_readings path is the deploy sensors
integration suite.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eeper.api.ingestion import SensorIngestor
from eeper.api.mqtt_provisioner import MqttProvisioner, device_topic_prefix, device_username
from eeper.api.schemas import SensorMessage


def _msg(topic: str, payload: str | bytes) -> SimpleNamespace:
    return SimpleNamespace(
        topic=topic, payload=payload.encode() if isinstance(payload, str) else payload
    )


def _good_payload(**over: object) -> str:
    return json.dumps(
        {"ts": 1.0, "type": "movement", "value": 0.5, "unit": "index", "quality": 0.9, **over}
    )


# ── the wire contract ─────────────────────────────────────────────────────────


def test_valid_sensor_message_parses() -> None:
    m = SensorMessage.model_validate_json(_good_payload())
    assert m.type == "movement" and m.quality == 0.9


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        json.dumps(
            {"ts": 1.0, "type": "movement", "value": 0.5, "unit": "index"}
        ),  # missing quality
        json.dumps(
            {"ts": 1.0, "type": "movement", "value": 0.5, "unit": "index", "quality": 2.0}
        ),  # out of range
        json.dumps(
            {"ts": 1.0, "type": "movement", "value": 0.5, "unit": "index", "quality": 0.9, "x": 1}
        ),  # extra field
        json.dumps(
            {"ts": 0, "type": "movement", "value": 0.5, "unit": "index", "quality": 0.9}
        ),  # ts must be > 0
    ],
)
def test_contract_rejects_malformed(payload: str) -> None:
    with pytest.raises(ValidationError):
        SensorMessage.model_validate_json(payload)


# ── the ingestion callback drops bad input without enqueuing ────────────────────


def _ingestor() -> SensorIngestor:
    return SensorIngestor(sessionmaker=None, settings=None)  # type: ignore[arg-type]


def test_valid_message_is_enqueued() -> None:
    ing = _ingestor()
    ing._on_message(None, None, _msg("eeper/dev/7/movement", _good_payload()))  # type: ignore[arg-type]
    assert ing._queue.qsize() == 1
    device_id, metric, value, quality, _ts = ing._queue.get_nowait()
    assert (device_id, metric, value, quality) == (7, "movement", 0.5, 0.9)


@pytest.mark.parametrize(
    "topic,payload",
    [
        ("eeper/dev/7/movement", "{not json"),  # malformed
        ("eeper/dev/7/movement", json.dumps({"ts": 1.0, "type": "movement"})),  # missing fields
        ("eeper/dev/7/movement", "x" * 5000),  # oversized
        ("eeper/dev/abc/movement", _good_payload()),  # non-numeric device id
        ("eeper/dev/7", _good_payload()),  # wrong topic shape
    ],
)
def test_bad_input_is_dropped(topic: str, payload: str) -> None:
    ing = _ingestor()
    ing._on_message(None, None, _msg(topic, payload))  # type: ignore[arg-type]
    assert ing._queue.qsize() == 0


# ── provisioner naming + the disabled (no-MQTT) posture ─────────────────────────


def test_device_identity_derives_from_id() -> None:
    assert device_username(7) == "dev-7"
    assert device_topic_prefix(7) == "eeper/dev/7/"


def test_provisioner_disabled_without_config() -> None:
    assert MqttProvisioner("", 8883, "", "", "").enabled is False
    assert MqttProvisioner("mqtt", 8883, "/ca", "eeper-api", "pw").enabled is True
