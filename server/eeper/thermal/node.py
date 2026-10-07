"""The thermal capture node (M6.1) — the glue that runs on the Pi.

Turns the pure :class:`ThermalPublisher` into a running node: reads config from the
environment, connects to the hardened MQTT bus AS the paired device (its per-device
credential + the deployment CA), maps the publisher's ``(metric, payload)`` onto
``eeper/dev/{id}/{metric}``, and drives the tick loop at the grid rate. The MLX90640
wiring lives in :class:`~eeper.thermal.sensor.MlxThermalSensor`; everything here is pure
except :func:`connect_mqtt` / :func:`main`, so the config, topic mapping, and loop are
tested without hardware or a broker.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from eeper.thermal.features import FeatureParams, GateParams
from eeper.thermal.publisher import MAX_HZ, ThermalPublisher
from eeper.thermal.sensor import ThermalSensor

_log = logging.getLogger("eeper.thermal.node")

# The server treats a presence input silent for 90 s as STALE, and a stale input fails open:
# the camera turns back on (eeper/api/presence.py FRESH_SECONDS). Devices also read as
# offline after the same 90 s. An idle interval anywhere near that would make low-power mode
# switch everything back on, so it is capped at half the window.
MAX_IDLE_INTERVAL_S = 45.0


@dataclass(frozen=True)
class NodeConfig:
    device_id: int
    mqtt_host: str
    mqtt_port: int = 8883
    mqtt_ca_cert: str = ""
    mqtt_username: str = ""  # the per-device account minted at pairing, e.g. "dev-7"
    mqtt_password: str = ""
    grid_hz: float = MAX_HZ  # ≤ 4 Hz; the publisher enforces the cap
    features_min_interval_s: float = 1.0
    i2c_bus: int = 1  # /dev/i2c-{bus}; 1 is the Pi's default header I²C
    # Presence tuning (docs/thermal-node.md). Defaults are the measured ones.
    enter_contrast_c: float = FeatureParams().enter_contrast_c
    min_on_seconds: float = GateParams().min_on_seconds
    min_off_seconds: float = GateParams().min_off_seconds
    # Low-power idle (see ThermalPublisher). 0 disables it.
    idle_interval_s: float = 0.0
    idle_after_s: float = 120.0

    @property
    def feature_params(self) -> FeatureParams:
        return FeatureParams(enter_contrast_c=self.enter_contrast_c)

    @property
    def gate_params(self) -> GateParams:
        return GateParams(min_on_seconds=self.min_on_seconds, min_off_seconds=self.min_off_seconds)

    @property
    def tick_interval_s(self) -> float:
        return 1.0 / max(0.1, min(self.grid_hz, MAX_HZ))

    @property
    def topic_prefix(self) -> str:
        return f"eeper/dev/{self.device_id}/"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> NodeConfig:
        def _num(key: str, default: float) -> float:
            raw = env.get(key)
            return float(raw) if raw else default

        device_id = int(env["EEPER_THERMAL_DEVICE_ID"])  # required
        return cls(
            device_id=device_id,
            mqtt_host=env.get("EEPER_MQTT_HOST", "eeper.local"),
            mqtt_port=int(_num("EEPER_MQTT_TLS_PORT", 8883)),
            mqtt_ca_cert=env.get("EEPER_MQTT_CA_CERT", ""),
            mqtt_username=env.get("EEPER_MQTT_USERNAME", f"dev-{device_id}"),
            mqtt_password=env.get("EEPER_MQTT_PASSWORD", ""),
            grid_hz=_num("EEPER_THERMAL_GRID_HZ", MAX_HZ),
            features_min_interval_s=_num("EEPER_THERMAL_FEATURES_INTERVAL_S", 1.0),
            i2c_bus=int(_num("EEPER_THERMAL_I2C_BUS", 1)),
            enter_contrast_c=_num(
                "EEPER_THERMAL_ENTER_CONTRAST_C", FeatureParams().enter_contrast_c
            ),
            min_on_seconds=_num("EEPER_THERMAL_MIN_ON_S", GateParams().min_on_seconds),
            min_off_seconds=_num("EEPER_THERMAL_MIN_OFF_S", GateParams().min_off_seconds),
            idle_interval_s=_idle_interval(_num("EEPER_THERMAL_IDLE_INTERVAL_S", 0.0)),
            idle_after_s=max(0.0, _num("EEPER_THERMAL_IDLE_AFTER_S", 120.0)),
        )


def _idle_interval(value: float) -> float:
    if value <= 0:
        return 0.0
    if value > MAX_IDLE_INTERVAL_S:
        _log.warning(
            "EEPER_THERMAL_IDLE_INTERVAL_S=%.0f is too slow: the server would treat the sensor "
            "as stale and turn the camera back on. Using %.0fs.",
            value,
            MAX_IDLE_INTERVAL_S,
        )
        return MAX_IDLE_INTERVAL_S
    return value


def topic_publisher(
    publish_raw: Callable[[str, str], object], prefix: str
) -> Callable[[str, dict[str, object]], None]:
    """Adapt the publisher's ``(metric, payload)`` sink onto the device's topic subtree:
    ``metric`` → ``{prefix}{metric}`` with a JSON body."""

    def publish(metric: str, payload: dict[str, object]) -> None:
        publish_raw(prefix + metric, json.dumps(payload))

    return publish


def build_publisher(
    config: NodeConfig,
    sensor: ThermalSensor,
    publish: Callable[[str, dict[str, object]], None],
    clock: Callable[[], float] = time.time,
) -> ThermalPublisher:
    return ThermalPublisher(
        sensor=sensor,
        publish=publish,
        clock=clock,
        features_min_interval_s=config.features_min_interval_s,
        feature_params=config.feature_params,
        gate_params=config.gate_params,
        idle_interval_s=config.idle_interval_s,
        idle_after_s=config.idle_after_s,
    )


def run(
    publisher: ThermalPublisher,
    tick_interval_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    iterations: int | None = None,
) -> None:
    """Drive the publisher forever (or ``iterations`` times, for tests), sleeping between
    ticks. ``tick`` never raises on a bad read, so the loop is self-healing."""
    count = 0
    while iterations is None or count < iterations:
        publisher.tick()
        sleep(tick_interval_s)
        count += 1


def connect_mqtt(config: NodeConfig):  # type: ignore[no-untyped-def]  # pragma: no cover
    """Connect to the broker AS the paired device (TLS + per-device credential)."""
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion

    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"thermal-{config.device_id}")
    client.username_pw_set(config.mqtt_username, config.mqtt_password)
    if config.mqtt_ca_cert:
        client.tls_set(ca_certs=config.mqtt_ca_cert)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    # Park a last-will under the device's own subtree (below the metric space so ingestion
    # ignores it), mirroring the reference sensor nodes.
    client.will_set(config.topic_prefix + "node/status", "offline", qos=1, retain=True)
    port = config.mqtt_port if config.mqtt_ca_cert else 1883
    client.connect(config.mqtt_host, port, keepalive=30)
    client.publish(config.topic_prefix + "node/status", "online", qos=1, retain=True)
    client.loop_start()
    return client


def main() -> None:  # pragma: no cover — hardware + broker entrypoint
    from eeper.thermal.sensor import MlxThermalSensor

    config = NodeConfig.from_env()
    client = connect_mqtt(config)
    sensor = MlxThermalSensor.open(bus=config.i2c_bus)
    publish = topic_publisher(
        lambda topic, body: client.publish(topic, body, qos=1), config.topic_prefix
    )
    publisher = build_publisher(config, sensor, publish)
    try:
        run(publisher, config.tick_interval_s)
    finally:
        client.loop_stop()
        client.disconnect()
