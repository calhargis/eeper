"""Derive the §4.5 thermal features from a raw 32×24 grid — pure, deterministic, no I/O.

The only thermal signal the fusion layer consumes (M6.3) is presence + warm-region shape.

**How presence is decided.** A body radiates enough that the hottest part of the scene sits
far above the room: on a real crib the hot region measures ~7 °C above background, steadily.
An empty crib has nothing to separate — the whole grid is room temperature give or take the
sensor's noise, so the hottest cell is *close to* the background. That gap, not the raw
temperature and not the warm-cell count alone, is the discriminator: CONTRAST is what tells
occupied from empty, and it is the primary gate here.

Counting cells above ``background + warm_delta_c`` (the original rule on its own) fails on an
empty crib because it has no notion of how much hotter those cells are. A gentle thermal
gradient — sun on a rail, a warm wall, the sensor's own drift — tips a few dozen cells over a
fixed offset and reads as a body. Worse, it does so intermittently: measured on a live
deployment, presence flipped with a MEDIAN RUN OF 2.3 SECONDS, and 62 of 70 presence runs
lasted under 30 s while the genuine ones lasted hours. That is dithering around a threshold,
not observation, so :class:`PresenceGate` adds hysteresis and a sustain requirement on top.

Surface temperatures only; nothing here is a body-temperature readout (§2).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# The MLX90640 array, row-major (must match schemas.THERMAL_*).
ROWS = 24
COLS = 32
CELLS = ROWS * COLS


@dataclass(frozen=True)
class FeatureParams:
    """Tunables for the extractor.

    The contrast defaults come from measured deployment data rather than intuition. Across
    475 frames of continuously occupied crib (40 minutes, background ~24 °C, peak ~32 °C),
    ``hot - background`` stayed between 5.9 and 8.3 °C — it never approached the acquire
    threshold. An empty crib has nothing but sensor noise and slow room gradients to work
    with. ``enter_contrast_c`` sits ~1.9 °C below the measured occupied floor, and
    ``exit_contrast_c`` lower still so a body that cools slightly (a baby settling under a
    blanket) does not drop out.
    """

    warm_delta_c: float = 2.5  # a cell is "warm" if this far above the room background
    min_area: float = 0.02  # minimum warm fraction (~15 cells) to call presence
    full_confidence_area: float = 0.08  # warm fraction at which confidence saturates

    # Contrast: how far the hot region sits above the room. The single hottest CELL is a
    # poor statistic — one stuck or noisy pixel would decide occupancy — so the hot end is
    # taken as a high percentile, which needs a genuine warm REGION to move.
    hot_percentile: float = 99.0  # ~the mean of the top ~8 cells of 768
    enter_contrast_c: float = 4.0  # below this, nothing in view is body-warm: no presence
    # ...but once a body IS present it takes a LOWER contrast to keep it. A baby who settles
    # deeper under a blanket radiates less through it, and losing them at the same number
    # that found them would report an empty crib for a child who never moved. One-sided
    # margin: the cost of holding presence a little too long is nil, the cost of dropping it
    # wrongly is the whole point of the monitor.
    exit_contrast_c: float = 2.8
    full_confidence_contrast_c: float = 6.0  # contrast at which confidence saturates


DEFAULT_FEATURE_PARAMS = FeatureParams()


@dataclass(frozen=True)
class ThermalFeatures:
    presence: bool
    presence_confidence: float
    warm_region_area: float
    warm_region_centroid: tuple[float, float] | None  # (row, col) normalized to [0, 1]
    # How far the hot region sits above the room background, in °C. Kept on the in-process
    # result for the gate and for logging; deliberately NOT published — the features wire
    # contract carries occupancy and shape, never a temperature (§2, §4.5).
    contrast_c: float = 0.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an ALREADY SORTED list."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def derive_features(
    temps: list[float], params: FeatureParams = DEFAULT_FEATURE_PARAMS
) -> ThermalFeatures:
    """Grid (768 °C, row-major) → §4.5 features.

    The room background is the median cell (robust while a body occupies a minority of the
    frame). Presence requires BOTH that something in view is genuinely body-warm relative to
    that background (``contrast >= enter_contrast_c``) AND that it covers a plausible area.
    The contrast test is what an empty crib fails: with no body present the hot end of the
    grid sits close to the background, exactly as the count-based rule alone cannot tell.
    """
    if len(temps) != CELLS:
        raise ValueError(f"expected {CELLS} cells, got {len(temps)}")

    ordered = sorted(temps)
    background = _percentile(ordered, 50.0)  # median — same value, one sort for both
    hot = _percentile(ordered, params.hot_percentile)
    contrast = hot - background

    threshold = background + params.warm_delta_c
    warm = [i for i, t in enumerate(temps) if t >= threshold]
    area = len(warm) / CELLS

    # Describe the warm region whenever there IS one, independent of the presence verdict.
    # Deriving shape only when this function says "present" would hide it during the band
    # where the gate holds presence on the lower threshold — the settling-baby case — and
    # publish "someone is there, confidence zero, location unknown" for exactly the child
    # this feature exists to keep track of. The boolean is a judgement; the evidence is not.
    confidence = 0.0
    centroid: tuple[float, float] | None = None
    if warm and area >= params.min_area:
        # Confidence is the WEAKER of the two pieces of evidence, so a large lukewarm region
        # and a tiny scorching speck both read as uncertain rather than either one winning.
        confidence = min(
            _clamp01(area / params.full_confidence_area),
            _clamp01(contrast / params.full_confidence_contrast_c),
        )
        mean_row = statistics.fmean(i // COLS for i in warm)
        mean_col = statistics.fmean(i % COLS for i in warm)
        centroid = (mean_row / (ROWS - 1), mean_col / (COLS - 1))

    return ThermalFeatures(
        presence=contrast >= params.enter_contrast_c and area >= params.min_area,
        presence_confidence=confidence,
        warm_region_area=area,
        warm_region_centroid=centroid,
        contrast_c=contrast,
    )


@dataclass(frozen=True)
class GateParams:
    """How long a change has to persist before it is believed.

    Asymmetric on purpose. Announcing an empty crib while the baby is still in it is the
    worse error, so releasing presence takes several times longer than acquiring it: a still,
    swaddled baby whose contrast dips for a few seconds must not read as "gone".
    """

    min_on_seconds: float = 8.0  # raw presence must hold this long before presence is reported
    min_off_seconds: float = 45.0  # ...and raw absence this long before it is withdrawn
    # A sustain window is evidence that something HELD, which requires observations. The
    # publisher drops frames on an I²C read failure, so without this a sensor that stalls
    # mid-window and recovers minutes later would have its elapsed time counted as if it had
    # been agreeing the whole time — one frame either side of an outage could flip the state.
    max_gap_seconds: float = 10.0


DEFAULT_GATE_PARAMS = GateParams()


class PresenceGate:
    """Turns per-frame observations into the presence actually reported.

    Two independent hysteresis mechanisms, because the flapping had two causes:

    * **Contrast hysteresis** — it takes ``enter_contrast_c`` to acquire presence but only
      ``exit_contrast_c`` to keep it, so a scene hovering at the boundary cannot oscillate.
    * **Time hysteresis** — a disagreeing observation starts a timer and the state flips only
      once the disagreement has been sustained; any agreeing frame resets it. A blip must
      outlast ``min_on_seconds`` to be believed, while a genuine occupancy lasting hours
      passes trivially. Measured on a live deployment, the median presence run was 2.3 s and
      62 of 70 runs were under 30 s — every one of those is noise this rejects.

    Stateful but pure: time is passed in, so a whole night replays deterministically in tests.
    """

    def __init__(
        self,
        params: FeatureParams = DEFAULT_FEATURE_PARAMS,
        gate: GateParams = DEFAULT_GATE_PARAMS,
    ) -> None:
        self._params = params
        self._gate = gate
        self._state = False  # reported presence
        self._pending_since: float | None = None  # when the disagreement started
        self._last_seen: float | None = None  # when the previous observation arrived

    @property
    def state(self) -> bool:
        return self._state

    def _observe(self, features: ThermalFeatures) -> bool:
        """Is a body in view right now? Judged against the acquire threshold when nothing is
        there, and the (lower) hold threshold once something is — the asymmetry that keeps a
        settling baby from being lost."""
        floor = self._params.exit_contrast_c if self._state else self._params.enter_contrast_c
        return features.contrast_c >= floor and features.warm_region_area >= self._params.min_area

    def update(self, features: ThermalFeatures, now: float) -> bool:
        """Feed one frame's features; return the presence to report."""
        raw_presence = self._observe(features)
        # A gap in the observations is not evidence of anything. Restart any window that
        # spans one, so elapsed time always corresponds to frames actually seen.
        gap = self._last_seen is not None and (now - self._last_seen) > self._gate.max_gap_seconds
        self._last_seen = now
        if gap:
            self._pending_since = now if raw_presence != self._state else None
            return self._state
        if raw_presence == self._state:
            self._pending_since = None  # the candidate change was not sustained
            return self._state
        if self._pending_since is None:
            self._pending_since = now
            return self._state
        # A clock that jumps backwards (NTP step on a Pi with no RTC) must not strand the
        # gate: treat any negative elapsed as a restart of the window rather than a value
        # that can never reach the threshold.
        elapsed = now - self._pending_since
        if elapsed < 0:
            self._pending_since = now
            return self._state
        needed = self._gate.min_on_seconds if raw_presence else self._gate.min_off_seconds
        if elapsed >= needed:
            self._state = raw_presence
            self._pending_since = None
        return self._state
