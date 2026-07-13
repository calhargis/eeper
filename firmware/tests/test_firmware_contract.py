"""M3.2 firmware contract + config-structure tests.

Two things are asserted here, cheaply and without the ESP toolchain (the real
`esphome config`/`compile` runs as its own CI step):

1. Every golden payload the reference nodes emit validates against the SAME
   ``SensorMessage`` contract the server's ingestion enforces — so the firmware and
   the server can never silently drift apart.
2. The ESPHome configs keep the properties that make a node a safe citizen on the
   hardened bus: TLS on 8883, a CA, HA discovery OFF, an SNTP clock, and every
   published topic confined to the device's own ``eeper/dev/{id}/`` ACL subtree
   (else the broker disconnects the node, or ingestion mis-reads housekeeping as data).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__).resolve().parent
_FIRMWARE = _HERE.parent
_ESPHOME = _FIRMWARE / "esphome"
# The server package is import-light for schemas (pydantic only); add it to the path so
# we validate against the real contract without installing the whole API.
sys.path.insert(0, str(_FIRMWARE.parent / "server"))

from eeper.api.schemas import SensorMessage  # noqa: E402

_DEVICE_PREFIX = "eeper/dev/${device_id}/"


class _TagTolerantLoader(yaml.SafeLoader):
    """Load ESPHome YAML while treating its custom ``!secret``/``!lambda``/``!include``
    tags as their raw scalar/collection value, so we can inspect structure without the
    ESPHome runtime."""


def _keep_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_TagTolerantLoader.add_multi_constructor("!", _keep_tag)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=_TagTolerantLoader)  # noqa: S506 (trusted repo files)


# ── 1. payload contract ───────────────────────────────────────────────────────

_PAYLOADS = sorted((_HERE / "payloads").glob("*.json"))


@pytest.mark.parametrize("payload_path", _PAYLOADS, ids=lambda p: p.name)
def test_golden_payload_matches_contract(payload_path: Path) -> None:
    """Each emitted reading validates against the server's SensorMessage schema."""
    model = SensorMessage.model_validate_json(payload_path.read_text())
    assert model.ts > 0
    assert 0.0 <= model.quality <= 1.0
    assert model.type and model.unit


def test_payloads_cover_both_node_types() -> None:
    """Guard against an empty glob silently passing the parametrized test."""
    names = {p.name for p in _PAYLOADS}
    assert {"mmwave_movement.json", "mmwave_presence.json", "pir_movement.json"} <= names


# ── 2. base package: safe-citizen invariants ──────────────────────────────────


def test_base_mqtt_is_tls_with_ca_and_discovery_off() -> None:
    mqtt = _load(_ESPHOME / "common" / "eeper-base.yaml")["mqtt"]
    assert mqtt["port"] == 8883, "must use the broker's TLS listener"
    assert mqtt["certificate_authority"], "must pin the deployment CA"
    assert mqtt["discovery"] is False, "HA discovery targets homeassistant/# (ACL-denied)"


def test_base_housekeeping_stays_in_device_subtree() -> None:
    mqtt = _load(_ESPHOME / "common" / "eeper-base.yaml")["mqtt"]
    # topic_prefix + birth/will must all live under the device's own ACL subtree, and
    # below the eeper/dev/+/+ metric space so ingestion doesn't parse them as readings.
    assert mqtt["topic_prefix"].startswith(_DEVICE_PREFIX + "node")
    for key in ("birth_message", "will_message"):
        assert mqtt[key]["topic"].startswith(_DEVICE_PREFIX + "node/")


def test_base_has_sntp_clock() -> None:
    time_cfg = _load(_ESPHOME / "common" / "eeper-base.yaml")["time"]
    platforms = {entry.get("platform") for entry in time_cfg}
    assert "sntp" in platforms, "ts needs a real clock or the node reads offline forever"


def test_publish_script_targets_device_metric_space() -> None:
    # The contract JSON + topic are built in a lambda; assert on the raw text.
    raw = (_ESPHOME / "common" / "eeper-base.yaml").read_text()
    assert f'"{_DEVICE_PREFIX}"' in raw, "readings must publish under eeper/dev/{id}/"
    for field in ("\\\"ts\\\"", "\\\"type\\\"", "\\\"value\\\"", "\\\"unit\\\"", "\\\"quality\\\""):
        assert field in raw, f"contract field {field} missing from the publish lambda"


# ── 2b. device configs include the base and are scoped by device_id ────────────


@pytest.mark.parametrize("name", ["eeper-mmwave.yaml", "eeper-pir.yaml"])
def test_device_config_includes_base_and_names_by_id(name: str) -> None:
    cfg = _load(_ESPHOME / name)
    assert cfg["packages"]["eeper"] == "common/eeper-base.yaml"
    assert "${device_id}" in cfg["esphome"]["name"]
    assert "device_id" in cfg["substitutions"]


def test_secrets_example_has_every_referenced_key() -> None:
    example = _load(_ESPHOME / "secrets.yaml.example")
    assert {"wifi_ssid", "wifi_password", "eeper_mqtt_password", "eeper_mqtt_ca"} <= set(example)
    assert "BEGIN CERTIFICATE" in example["eeper_mqtt_ca"]
