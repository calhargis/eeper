"""Derive the §4.5 thermal features from a raw 32×24 grid — pure, deterministic, no I/O.

The only thermal signal the fusion layer consumes (M6.3) is presence + warm-region shape.
This is a deterministic BASELINE extractor: its job in M6.1 is to turn a valid grid into
valid features. Whether those features are ACCURATE enough to earn fusion integration is
the explicit M6.2 characterization gate — not decided here.

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
    """Tunables for the baseline extractor. M6.2 re-fits these on the characterization
    corpus; the defaults are only meant to be sane, not accurate."""

    warm_delta_c: float = 2.5  # a cell is "warm" if this far above the room background
    min_area: float = 0.02  # minimum warm fraction (~15 cells) to call presence
    full_confidence_area: float = 0.08  # warm fraction at which confidence saturates


DEFAULT_FEATURE_PARAMS = FeatureParams()


@dataclass(frozen=True)
class ThermalFeatures:
    presence: bool
    presence_confidence: float
    warm_region_area: float
    warm_region_centroid: tuple[float, float] | None  # (row, col) normalized to [0, 1]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def derive_features(
    temps: list[float], params: FeatureParams = DEFAULT_FEATURE_PARAMS
) -> ThermalFeatures:
    """Grid (768 °C, row-major) → §4.5 features. The room background is the median cell (a
    robust estimate under a few warm cells); cells ``warm_delta_c`` above it are the warm
    region. Presence needs a warm region of at least ``min_area``."""
    if len(temps) != CELLS:
        raise ValueError(f"expected {CELLS} cells, got {len(temps)}")

    background = statistics.median(temps)
    threshold = background + params.warm_delta_c
    warm = [i for i, t in enumerate(temps) if t >= threshold]
    area = len(warm) / CELLS

    if area < params.min_area:
        return ThermalFeatures(
            presence=False,
            presence_confidence=0.0,
            warm_region_area=area,
            warm_region_centroid=None,
        )

    confidence = _clamp01(area / params.full_confidence_area)
    mean_row = statistics.fmean(i // COLS for i in warm)
    mean_col = statistics.fmean(i % COLS for i in warm)
    centroid = (mean_row / (ROWS - 1), mean_col / (COLS - 1))
    return ThermalFeatures(
        presence=True,
        presence_confidence=confidence,
        warm_region_area=area,
        warm_region_centroid=centroid,
    )
