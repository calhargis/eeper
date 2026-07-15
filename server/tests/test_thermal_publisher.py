"""M6.1 publisher loop invariants: a valid frame emits both contract messages; a read
failure or a malformed frame is dropped (never a bad or stale grid on the wire), health
degrades and recovers automatically, and the grid rate is capped at 4 Hz."""

from __future__ import annotations

from eeper.api.schemas import THERMAL_CELLS, ThermalFeaturesMessage, ThermalGridMessage
from eeper.thermal.publisher import (
    FEATURES_METRIC,
    GRID_METRIC,
    MAX_HZ,
    ThermalPublisher,
)


def _good(value: float = 25.0) -> list[float]:
    return [value] * THERMAL_CELLS


class ScriptedSensor:
    """Returns the queued frames in order; once drained, a steady good frame."""

    def __init__(self, frames: list[list[float] | None]) -> None:
        self._frames = list(frames)
        self.reads = 0

    def read(self) -> list[float] | None:
        self.reads += 1
        return self._frames.pop(0) if self._frames else _good()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _publisher(sensor: ScriptedSensor, clock: Clock):  # type: ignore[no-untyped-def]
    sink: list[tuple[str, dict[str, object]]] = []
    pub = ThermalPublisher(sensor=sensor, publish=lambda m, p: sink.append((m, p)), clock=clock)
    return pub, sink


def test_valid_frame_publishes_both_validated_messages() -> None:
    pub, sink = _publisher(ScriptedSensor([_good()]), Clock())
    assert pub.tick() is True
    assert [m for m, _ in sink] == [GRID_METRIC, FEATURES_METRIC]
    # Round-trips back through the contract → what's on the wire is always valid.
    ThermalGridMessage.model_validate(sink[0][1])
    ThermalFeaturesMessage.model_validate(sink[1][1])
    assert pub.stats.published == 1


def test_read_failure_is_dropped_no_crash_and_recovers() -> None:
    clock = Clock()
    pub, sink = _publisher(ScriptedSensor([None, None, _good()]), clock)
    assert pub.tick() is False  # I²C read failure
    clock.advance(1.0)
    assert pub.tick() is False  # still failing
    clock.advance(1.0)
    assert pub.tick() is True  # auto-recovers
    assert pub.stats.read_failures == 2
    assert pub.stats.published == 1
    assert pub.stats.fail_streak == 0
    assert len(sink) == 2  # only the recovered frame (grid + features); no stale re-publish


def test_malformed_frame_dropped_with_quality_degradation() -> None:
    clock = Clock()
    truncated = _good()[:-1]  # wrong length
    nan_frame = _good()
    nan_frame[0] = float("nan")
    pub, sink = _publisher(ScriptedSensor([truncated, nan_frame, _good()]), clock)
    assert pub.tick() is False
    clock.advance(1.0)
    assert pub.tick() is False
    clock.advance(1.0)
    assert pub.tick() is True
    assert pub.stats.dropped_invalid == 2
    # The recovered grid's quality is marked down after the 2-frame failure streak.
    assert 0.0 < float(sink[0][1]["quality"]) < 1.0


def test_grid_rate_capped_at_max_hz() -> None:
    clock = Clock()
    pub, _sink = _publisher(ScriptedSensor([]), clock)  # always a good frame available
    published = 0
    for _ in range(100):  # 100 ticks over 1.0 s of virtual time
        if pub.tick():
            published += 1
        clock.advance(0.01)
    assert 3 <= published <= int(MAX_HZ) + 1  # ~4 grids in one second
    assert pub.stats.rate_skipped > 0


def test_features_are_low_rate_relative_to_grids() -> None:
    # §4.5: the grid is 2–4 Hz; the derived features are low-rate. Over one second of
    # 4 Hz grids, features are emitted at most once (the default 1 s cadence).
    clock = Clock()
    pub, sink = _publisher(ScriptedSensor([]), clock)
    for _ in range(100):
        pub.tick()
        clock.advance(0.01)
    features = sum(1 for m, _ in sink if m == FEATURES_METRIC)
    assert features < pub.stats.published  # strictly fewer feature messages than grids
    assert features == pub.stats.features_published == 1
