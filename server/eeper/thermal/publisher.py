"""The thermal node publish loop (M6.1, §4.5) — pure and testable.

Each :meth:`ThermalPublisher.tick` reads one frame and, if it is a valid grid, emits the
§4.5 grid + derived-features messages through a publish sink. The invariants the M6.1
[AUTO] criteria pin down:

* **never publish a bad grid** — a read failure (``None``) or a structurally invalid frame
  (wrong length, non-finite / out-of-range temps) is dropped and counted; the last good
  grid is never re-published to fill the gap;
* **rate discipline** — grids are emitted at most :data:`MAX_HZ` regardless of how often
  ``tick`` is called;
* **quality degrades, it doesn't lie** — the ``quality`` field dips right after a failure
  streak and recovers, so a consumer (and device health) can see the wobble.

No MQTT or hardware here — the publish sink and the clock are injected. The node
entrypoint (M6.1 slice 2) wires the real MLX90640 + paho MQTT over TLS on top.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from eeper.api.schemas import (
    THERMAL_CELLS,
    ThermalFeaturesMessage,
    ThermalGridMessage,
)
from eeper.thermal.features import (
    DEFAULT_GATE_PARAMS,
    FeatureParams,
    GateParams,
    PresenceGate,
    derive_features,
)
from eeper.thermal.sensor import ThermalSensor

GRID_METRIC = "thermal"
FEATURES_METRIC = "thermal_features"
_log = logging.getLogger("eeper.thermal.publisher")

MAX_HZ = 4.0
_MIN_INTERVAL_S = 1.0 / MAX_HZ

# Must match schemas.ThermalGridMessage's envelope, so a frame the publisher accepts always
# validates against the contract.
_T_MIN = -40.0
_T_MAX = 300.0


@dataclass
class PublishStats:
    """Observable health of the publisher — the raw material for device health."""

    published: int = 0  # grids emitted
    features_published: int = 0  # low-rate features messages emitted
    read_failures: int = 0  # sensor.read() returned None
    dropped_invalid: int = 0  # a structurally malformed frame
    rate_skipped: int = 0  # ticks skipped to hold the rate cap
    idle_skipped: int = 0  # ticks skipped because the node is idling over an empty crib
    fail_streak: int = 0  # consecutive bad reads right now (0 == healthy)


def _is_valid_grid(temps: object) -> bool:
    return (
        isinstance(temps, list)
        and len(temps) == THERMAL_CELLS
        and all(
            isinstance(t, (int, float)) and math.isfinite(t) and _T_MIN <= t <= _T_MAX
            for t in temps
        )
    )


def _quality(fail_streak: int) -> float:
    """A clean read is 1.0; the first good frames after a failure streak are marked down
    (and recover as the streak clears), so quality reflects real read integrity."""
    return max(0.5, 1.0 - 0.1 * min(fail_streak, 5))


@dataclass
class ThermalPublisher:
    sensor: ThermalSensor
    publish: Callable[[str, dict[str, object]], None]  # (metric, payload) sink
    clock: Callable[[], float]  # unix seconds; used for both the rate gate and message ts
    feature_params: FeatureParams = field(default_factory=FeatureParams)
    gate_params: GateParams = field(default_factory=lambda: DEFAULT_GATE_PARAMS)
    # §4.5: the grid is 2–4 Hz for characterization; the derived features are LOW-rate.
    features_min_interval_s: float = 1.0
    # Idle mode. With nobody in the crib there is nothing to watch closely, so the node
    # samples one frame every `idle_interval_s` instead of several a second. 0 disables
    # it (the default — the heatmap stays live at full rate). The node decides this from
    # its OWN presence verdict, which is what makes it possible at all: the broker only
    # lets a device publish, so nothing upstream could tell it to slow down.
    idle_interval_s: float = 0.0
    idle_after_s: float = 120.0  # reported absence must last this long before idling
    stats: PublishStats = field(default_factory=PublishStats)
    _last_publish: float = -1e18
    _last_features: float = -1e18
    _last_read: float = -1e18
    _absent_since: float | None = None  # when reported presence last became False
    _gate: PresenceGate = field(init=False)

    def __post_init__(self) -> None:
        # Debounces the per-frame verdict. It lives HERE, on the node, so every consumer
        # downstream — the stored history, fusion, the UI — sees one already-stable answer
        # instead of each having to re-derive its own idea of what a flicker means.
        self._gate = PresenceGate(self.feature_params, self.gate_params)

    def is_idle(self, now: float) -> bool:
        """Idle once the crib has been reported empty for `idle_after_s`. Anything else —
        presence, a candidate the gate is still confirming, idle mode disabled — samples at
        the full rate."""
        return (
            self.idle_interval_s > 0
            and self._absent_since is not None
            and now - self._absent_since >= self.idle_after_s
        )

    def tick(self) -> bool:
        """Read + maybe publish one frame. Returns True iff a grid was published."""
        now = self.clock()
        idle = self.is_idle(now)
        if idle:
            if now - self._last_read < self.idle_interval_s:
                self.stats.idle_skipped += 1
                return False
        elif now - self._last_publish < _MIN_INTERVAL_S:
            self.stats.rate_skipped += 1
            return False

        self._last_read = now
        temps = self.sensor.read()
        if temps is None:
            self.stats.read_failures += 1
            self.stats.fail_streak += 1
            return False  # never re-publish a stale grid to cover a read failure
        if not _is_valid_grid(temps):
            self.stats.dropped_invalid += 1
            self.stats.fail_streak += 1
            return False

        quality = _quality(self.stats.fail_streak)
        self.stats.fail_streak = 0

        grid_msg = ThermalGridMessage(
            ts=now,
            grid=temps,
            t_min=min(temps),
            t_max=max(temps),
            t_mean=sum(temps) / len(temps),
            quality=quality,
        )
        # Validated by construction (pydantic) → a malformed grid can never reach the wire.
        self.publish(GRID_METRIC, grid_msg.model_dump())
        self._last_publish = now
        self.stats.published += 1

        # Derived features ride at their own (lower) cadence — the only signal fusion reads.
        if now - self._last_features >= self.features_min_interval_s:
            feats = derive_features(temps, self.feature_params)
            was_present = self._gate.state
            presence = self._gate.update(feats, now)
            self._track_idle(feats.presence, presence, now, idle)
            if presence != was_present:
                # Only on a transition, so this stays quiet in steady state while still
                # giving an operator the two numbers they need to tune the thresholds
                # against their own room (see docs/thermal-node.md).
                _log.info(
                    "presence %s (contrast %.1f C, warm area %.3f)",
                    "detected" if presence else "cleared",
                    feats.contrast_c,
                    feats.warm_region_area,
                )
            # Shape follows the presence actually being REPORTED: publishing a centroid while
            # presence is false would let a consumer resurrect the very blip the gate just
            # suppressed. Confidence is the live evidence either way — during the release
            # hold after a baby is lifted out it correctly falls to zero while presence is
            # still held, which reads as "still reporting present, no longer seeing anything"
            # rather than as certainty the detector does not have.
            centroid_src = feats.warm_region_centroid if presence else None
            centroid = list(centroid_src) if centroid_src else None
            feat_msg = ThermalFeaturesMessage(
                ts=now,
                presence=presence,
                presence_confidence=feats.presence_confidence if presence else 0.0,
                warm_region_area=feats.warm_region_area,
                warm_region_centroid=centroid,
            )
            self.publish(FEATURES_METRIC, feat_msg.model_dump())
            self._last_features = now
            self.stats.features_published += 1
        return True

    def _track_idle(self, raw: bool, reported: bool, now: float, was_idle: bool) -> None:
        """Advance the idle clock from this frame's verdicts.

        Any RAW candidate wakes the node immediately — not the gated verdict, which needs
        8 s of frames to confirm and would never get them at one frame every 15 s. Waking
        on the raw signal is what lets a baby being put down be confirmed at full rate. A
        candidate that turns out to be a blip simply lets the idle clock run again.
        """
        if reported or raw:
            if was_idle:
                _log.info("possible presence — sampling at full rate to confirm")
            self._absent_since = None
            return
        if self._absent_since is None:
            self._absent_since = now
        if not was_idle and self.is_idle(now):
            _log.info(
                "crib empty for %.0fs — idling, one frame every %.0fs",
                self.idle_after_s,
                self.idle_interval_s,
            )
