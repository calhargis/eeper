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

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from eeper.api.schemas import (
    THERMAL_CELLS,
    ThermalFeaturesMessage,
    ThermalGridMessage,
)
from eeper.thermal.features import FeatureParams, derive_features
from eeper.thermal.sensor import ThermalSensor

GRID_METRIC = "thermal"
FEATURES_METRIC = "thermal_features"
MAX_HZ = 4.0
_MIN_INTERVAL_S = 1.0 / MAX_HZ

# Must match schemas.ThermalGridMessage's envelope, so a frame the publisher accepts always
# validates against the contract.
_T_MIN = -40.0
_T_MAX = 300.0


@dataclass
class PublishStats:
    """Observable health of the publisher — the raw material for device health."""

    published: int = 0
    read_failures: int = 0  # sensor.read() returned None
    dropped_invalid: int = 0  # a structurally malformed frame
    rate_skipped: int = 0  # ticks skipped to hold the rate cap
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
    stats: PublishStats = field(default_factory=PublishStats)
    _last_publish: float = -1e18

    def tick(self) -> bool:
        """Read + maybe publish one frame. Returns True iff a grid was published."""
        now = self.clock()
        if now - self._last_publish < _MIN_INTERVAL_S:
            self.stats.rate_skipped += 1
            return False

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
        feats = derive_features(temps, self.feature_params)
        centroid = list(feats.warm_region_centroid) if feats.warm_region_centroid else None
        feat_msg = ThermalFeaturesMessage(
            ts=now,
            presence=feats.presence,
            presence_confidence=feats.presence_confidence,
            warm_region_area=feats.warm_region_area,
            warm_region_centroid=centroid,
        )

        # Validated by construction (pydantic) → a malformed grid can never reach the wire.
        self.publish(GRID_METRIC, grid_msg.model_dump())
        self.publish(FEATURES_METRIC, feat_msg.model_dump())
        self._last_publish = now
        self.stats.published += 1
        return True
