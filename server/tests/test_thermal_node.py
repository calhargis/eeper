"""M6.1 slice 3: the thermal capture node's testable glue — config from the environment,
the ``(metric, payload)`` → ``eeper/dev/{id}/{metric}`` topic mapping, the self-healing
tick loop, and the MLX90640 read adapter (checksum/read failure → ``None``). The hardware
open() + broker connect are the [MANUAL] bench; everything here runs without either."""

from __future__ import annotations

import json
import random

from eeper.api.schemas import THERMAL_CELLS, ThermalFeaturesMessage, ThermalGridMessage
from eeper.thermal.node import NodeConfig, build_publisher, run, topic_publisher
from eeper.thermal.sensor import (
    MlxThermalSensor,
    Scene,
    WarmBlob,
    render,
    repair_dead_pixels,
)


def _good() -> list[float]:
    return render(Scene(ambient_c=21.0, blobs=(WarmBlob(12.0, 16.0, 3.0, 8.0),)), random.Random(4))


class _SteadySensor:
    def read(self) -> list[float] | None:
        return _good()


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_config_from_env() -> None:
    cfg = NodeConfig.from_env(
        {
            "EEPER_THERMAL_DEVICE_ID": "7",
            "EEPER_MQTT_HOST": "eeper.lan",
            "EEPER_MQTT_CA_CERT": "/run/ca.crt",
            "EEPER_MQTT_PASSWORD": "secret",
            "EEPER_THERMAL_GRID_HZ": "2",
        }
    )
    assert cfg.device_id == 7
    assert cfg.mqtt_username == "dev-7"  # derived from the id when not given
    assert cfg.topic_prefix == "eeper/dev/7/"
    assert cfg.tick_interval_s == 0.5  # 2 Hz
    assert cfg.mqtt_host == "eeper.lan" and cfg.mqtt_password == "secret"


def test_grid_hz_is_capped_at_max() -> None:
    cfg = NodeConfig.from_env({"EEPER_THERMAL_DEVICE_ID": "1", "EEPER_THERMAL_GRID_HZ": "100"})
    assert cfg.tick_interval_s == 0.25  # never faster than 4 Hz


def test_topic_publisher_maps_metric_to_device_subtree() -> None:
    sent: list[tuple[str, str]] = []
    publish = topic_publisher(lambda topic, body: sent.append((topic, body)), "eeper/dev/7/")
    publish("thermal_features", {"presence": True})
    assert sent == [("eeper/dev/7/thermal_features", json.dumps({"presence": True}))]


def test_run_loop_publishes_contract_messages_to_the_right_topics() -> None:
    sent: list[tuple[str, str]] = []
    cfg = NodeConfig(device_id=7, mqtt_host="h")
    publish = topic_publisher(lambda topic, body: sent.append((topic, body)), cfg.topic_prefix)
    pub = build_publisher(cfg, _SteadySensor(), publish, clock=_Clock())
    run(pub, cfg.tick_interval_s, sleep=lambda _dt: None, iterations=1)

    topics = [t for t, _ in sent]
    assert topics == ["eeper/dev/7/thermal", "eeper/dev/7/thermal_features"]
    grid = json.loads(sent[0][1])
    ThermalGridMessage.model_validate(grid)  # valid §4.5 grid on the wire
    ThermalFeaturesMessage.model_validate(json.loads(sent[1][1]))
    assert len(grid["grid"]) == THERMAL_CELLS


def test_run_loop_is_self_healing_on_read_failures() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.n = 0

        def read(self) -> list[float] | None:
            self.n += 1
            return None if self.n <= 2 else _good()

    sent: list[tuple[str, str]] = []
    cfg = NodeConfig(device_id=1, mqtt_host="h")
    publish = topic_publisher(lambda topic, body: sent.append((topic, body)), cfg.topic_prefix)
    pub = build_publisher(cfg, _Flaky(), publish, clock=_Clock())
    run(pub, cfg.tick_interval_s, sleep=lambda _dt: None, iterations=5)  # no exception raised
    assert pub.stats.read_failures == 2
    assert pub.stats.published >= 1  # recovered and published after the failures


# ── the MLX90640 read adapter (no hardware) ──────────────────────────────────


class _FakeDriver:
    def __init__(self, fill: float = 24.0, raises: type[Exception] | None = None) -> None:
        self._fill = fill
        self._raises = raises

    def getFrame(self, buf: list[float]) -> None:  # noqa: N802 — vendor API name
        if self._raises is not None:
            raise self._raises("checksum")
        for i in range(len(buf)):
            buf[i] = self._fill


def test_mlx_read_returns_full_grid() -> None:
    sensor = MlxThermalSensor(_FakeDriver(fill=25.5))
    frame = sensor.read()
    assert frame is not None and len(frame) == THERMAL_CELLS and set(frame) == {25.5}


def test_mlx_read_returns_none_on_driver_error() -> None:
    for exc in (ValueError, RuntimeError, OSError):
        assert MlxThermalSensor(_FakeDriver(raises=exc)).read() is None


# ── dead-pixel repair (real MLX90640 emits the odd out-of-range cell) ─────────


def test_repair_passes_a_clean_grid_through() -> None:
    grid = _good()
    assert repair_dead_pixels(grid) is grid  # nothing bad → same object, untouched


def test_repair_fixes_a_lone_dead_pixel() -> None:
    grid = _good()
    grid[320] = -273.15  # the MLX90640 dead-pixel sentinel
    out = repair_dead_pixels(grid)
    assert out is not None
    assert -40.0 <= out[320] <= 300.0  # back inside the §4.5 range
    assert abs(out[320] - 21.0) < 6.0  # interpolated from its ~ambient neighbours


def test_repair_drops_a_mostly_bad_frame() -> None:
    grid = _good()
    for i in range(20):  # more than the tolerated handful → a corrupt read
        grid[i] = -273.15
    assert repair_dead_pixels(grid) is None


def test_mlx_read_repairs_dead_pixels() -> None:
    class _OneDead:
        def getFrame(self, buf: list[float]) -> None:  # noqa: N802 — vendor API name
            for i in range(len(buf)):
                buf[i] = 21.0
            buf[320] = -273.15

    frame = MlxThermalSensor(_OneDead()).read()
    assert frame is not None and -40.0 <= frame[320] <= 300.0


# ── presence tuning + low-power idle reach the publisher ─────────────────────


def test_documented_presence_tunables_are_actually_read() -> None:
    """Regression: docs/thermal-node.md documented ENTER_CONTRAST_C, MIN_ON_S and MIN_OFF_S
    for months while the code never read them — the edit adding them had silently not
    applied. A setting an operator can set but that does nothing is worse than no setting.
    This pins both halves: the env is parsed, AND the values reach the running publisher."""
    cfg = NodeConfig.from_env(
        {
            "EEPER_THERMAL_DEVICE_ID": "7",
            "EEPER_THERMAL_ENTER_CONTRAST_C": "5.5",
            "EEPER_THERMAL_MIN_ON_S": "3",
            "EEPER_THERMAL_MIN_OFF_S": "90",
            "EEPER_THERMAL_IDLE_INTERVAL_S": "15",
            "EEPER_THERMAL_IDLE_AFTER_S": "60",
        }
    )
    pub = build_publisher(cfg, _SteadySensor(), lambda m, p: None, clock=_Clock())
    assert pub.feature_params.enter_contrast_c == 5.5
    assert pub.gate_params.min_on_seconds == 3
    assert pub.gate_params.min_off_seconds == 90
    assert pub.idle_interval_s == 15
    assert pub.idle_after_s == 60


def test_idle_is_off_unless_asked_for() -> None:
    """Idle mode slows the heatmap, so it must be an explicit choice, not a surprise."""
    cfg = NodeConfig.from_env({"EEPER_THERMAL_DEVICE_ID": "7"})
    assert cfg.idle_interval_s == 0


def test_an_idle_interval_that_would_look_stale_is_capped() -> None:
    """The server treats a presence input silent for 90 s as stale and FAILS OPEN — it turns
    the camera back on. An idle interval near that would make low-power mode undo itself, so
    it is capped well inside the window rather than trusted."""
    cfg = NodeConfig.from_env(
        {"EEPER_THERMAL_DEVICE_ID": "7", "EEPER_THERMAL_IDLE_INTERVAL_S": "120"}
    )
    assert 0 < cfg.idle_interval_s <= 45
