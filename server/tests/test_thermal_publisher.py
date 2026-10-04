"""M6.1 publisher loop invariants: a valid frame emits both contract messages; a read
failure or a malformed frame is dropped (never a bad or stale grid on the wire), health
degrades and recovers automatically, and the grid rate is capped at 4 Hz."""

from __future__ import annotations

import random

from eeper.api.schemas import THERMAL_CELLS, ThermalFeaturesMessage, ThermalGridMessage
from eeper.thermal.features import derive_features
from eeper.thermal.publisher import (
    FEATURES_METRIC,
    GRID_METRIC,
    MAX_HZ,
    ThermalPublisher,
)
from eeper.thermal.sensor import Scene, WarmBlob, render


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


def test_shape_is_suppressed_while_the_gate_reports_absent() -> None:
    """A consumer must not be able to reconstruct a suppressed blip from the leftovers. While
    the gate is holding presence back, the published centroid and confidence report the
    absence too — otherwise a 'no presence, but here is exactly where it is, confidence 0.9'
    message would invite exactly the flicker the gate exists to remove."""
    grid = render(Scene(ambient_c=21.0, blobs=(WarmBlob(12.0, 16.0, 4.5, 9.0),)), random.Random(21))
    clock = Clock()
    # The same body stays in view for every tick (ScriptedSensor falls back to an EMPTY
    # frame once drained, which would end the occupancy rather than sustain it).
    pub, sent = _publisher(ScriptedSensor([grid] * 25), clock)
    pub.tick()
    feats = [p for m, p in sent if m == "thermal_features"]
    assert feats, "a features message should have been emitted"
    first = feats[0]
    assert first["presence"] is False, "the acquire window has not elapsed yet"
    assert first["warm_region_centroid"] is None
    assert first["presence_confidence"] == 0.0
    # The raw frame really does contain a body — the suppression is the gate, not the scene.
    assert derive_features(grid).presence is True

    # Once the frame has persisted past the acquire window, everything is reported.
    for _ in range(20):
        clock.advance(1.0)
        pub.tick()
    last = [p for m, p in sent if m == "thermal_features"][-1]
    assert last["presence"] is True
    assert last["warm_region_centroid"] is not None
    assert last["presence_confidence"] > 0.0


# ── low-power idle over an empty crib ────────────────────────────────────────


class _SwitchableSensor:
    """An empty crib until `occupied` is flipped, then a warm body."""

    def __init__(self) -> None:
        self.occupied = False
        self.reads = 0

    def read(self) -> list[float] | None:
        self.reads += 1
        blobs = (WarmBlob(12.0, 16.0, 4.5, 9.0),) if self.occupied else ()
        return render(Scene(ambient_c=23.5, blobs=blobs), random.Random(self.reads))


def _idle_publisher(sensor: _SwitchableSensor, clock: Clock):  # type: ignore[no-untyped-def]
    sink: list[tuple[str, dict[str, object]]] = []
    pub = ThermalPublisher(
        sensor=sensor,
        publish=lambda m, p: sink.append((m, p)),
        clock=clock,
        idle_interval_s=15.0,
        idle_after_s=120.0,
    )
    return pub, sink


def _run_for(pub: ThermalPublisher, clock: Clock, seconds: float, step: float = 0.25) -> None:
    end = clock.t + seconds
    while clock.t < end:
        pub.tick()
        clock.advance(step)


def test_an_empty_crib_drops_to_one_frame_every_idle_interval() -> None:
    sensor, clock = _SwitchableSensor(), Clock()
    pub, _ = _idle_publisher(sensor, clock)
    _run_for(pub, clock, 130)  # past idle_after_s
    assert pub.is_idle(clock.t)
    before = sensor.reads
    _run_for(pub, clock, 60)
    reads = sensor.reads - before
    assert 3 <= reads <= 5, f"~4 reads a minute at 15 s, not {reads}"


def test_idle_never_goes_quiet_long_enough_to_look_stale() -> None:
    """The server's staleness window is 90 s, and stale fails OPEN — so even idling, the
    node must keep reporting well inside it, or low-power mode would undo itself."""
    sensor, clock = _SwitchableSensor(), Clock()
    pub, sink = _idle_publisher(sensor, clock)
    _run_for(pub, clock, 400)
    ts = [p["ts"] for m, p in sink if m == "thermal_features"]
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]  # type: ignore[operator]
    assert max(gaps) <= 16, f"features went quiet for {max(gaps)}s"  # type: ignore[type-var]


def test_a_baby_put_down_wakes_the_node_and_is_confirmed_at_full_rate() -> None:
    """The point of waking on the RAW signal: the gate needs ~8 s of continuous frames to
    confirm presence, which one frame every 15 s could never supply."""
    sensor, clock = _SwitchableSensor(), Clock()
    pub, sink = _idle_publisher(sensor, clock)
    _run_for(pub, clock, 200)
    assert pub.is_idle(clock.t)

    sensor.occupied = True
    put_down = clock.t
    _run_for(pub, clock, 40)
    present = [p for m, p in sink if m == "thermal_features" and p["presence"]]
    assert present, "presence must be confirmed after waking"
    latency = present[0]["ts"] - put_down  # type: ignore[operator]
    # Worst case: one idle interval to notice + the gate's 8 s sustain + a frame or two.
    assert latency <= 15 + 8 + 3, f"took {latency}s to confirm a baby"
    assert not pub.is_idle(clock.t)


def test_a_blip_does_not_keep_the_node_awake_forever() -> None:
    """A false candidate that happens to land on an idle sample wakes the node — it has to,
    since it cannot tell a blip from a baby without looking closely. But once the crib
    stays empty it must settle back to idle rather than run at full rate indefinitely.

    (A blip that falls BETWEEN idle samples is never seen at all, which is the point of
    idling — so this deliberately holds the candidate until a sample catches it.)"""
    sensor, clock = _SwitchableSensor(), Clock()
    pub, _ = _idle_publisher(sensor, clock)
    _run_for(pub, clock, 200)
    assert pub.is_idle(clock.t)

    sensor.occupied = True
    for _ in range(int(20 / 0.25)):  # until an idle sample catches it
        pub.tick()
        clock.advance(0.25)
        if not pub.is_idle(clock.t):
            break
    assert not pub.is_idle(clock.t), "a sampled candidate must wake the node"

    sensor.occupied = False  # it was a blip
    _run_for(pub, clock, 130)
    assert pub.is_idle(clock.t), "it must settle back to idle once the crib stays empty"


def test_idle_still_publishes_a_slow_heatmap() -> None:
    """Without stream gating, someone may be watching the heatmap of an empty crib. It slows
    rather than freezes."""
    sensor, clock = _SwitchableSensor(), Clock()
    pub, sink = _idle_publisher(sensor, clock)
    _run_for(pub, clock, 130)
    n = len(sink)
    _run_for(pub, clock, 60)
    grids = [m for m, _ in sink[n:] if m == "thermal"]
    assert 3 <= len(grids) <= 5
