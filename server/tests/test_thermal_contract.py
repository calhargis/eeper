"""M6.1 §4.5 thermal wire contract: a valid grid + features validate; a truncated grid,
a non-finite temperature, an out-of-range temperature, an unknown field, or a
non-normalized centroid are all rejected — so a malformed frame can never validate (and
therefore is never published)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from eeper.api.schemas import THERMAL_CELLS, ThermalFeaturesMessage, ThermalGridMessage


def _grid(value: float = 21.0) -> list[float]:
    return [value] * THERMAL_CELLS


def test_valid_grid_and_features_validate() -> None:
    g = ThermalGridMessage(ts=1.0, grid=_grid(), t_min=21.0, t_max=21.0, t_mean=21.0, quality=0.9)
    assert len(g.grid) == THERMAL_CELLS
    f = ThermalFeaturesMessage(
        ts=1.0,
        presence=True,
        presence_confidence=0.5,
        warm_region_area=0.1,
        warm_region_centroid=[0.5, 0.5],
    )
    assert f.presence and f.warm_region_centroid == [0.5, 0.5]


def test_truncated_grid_rejected() -> None:
    with pytest.raises(ValidationError):
        ThermalGridMessage(ts=1.0, grid=_grid()[:-1], t_min=0, t_max=0, t_mean=0, quality=1.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 999.0, -100.0])
def test_non_finite_or_out_of_range_temp_rejected(bad: float) -> None:
    grid = _grid()
    grid[123] = bad
    with pytest.raises(ValidationError):
        ThermalGridMessage(ts=1.0, grid=grid, t_min=0, t_max=0, t_mean=0, quality=1.0)


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        ThermalGridMessage(
            ts=1.0,
            grid=_grid(),
            t_min=0,
            t_max=0,
            t_mean=0,
            quality=1.0,
            sneaky=1,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize("kwargs", [{"ts": 0.0}, {"quality": 1.5}, {"quality": -0.1}])
def test_grid_field_bounds(kwargs: dict[str, float]) -> None:
    base: dict[str, object] = {
        "ts": 1.0,
        "grid": _grid(),
        "t_min": 0,
        "t_max": 0,
        "t_mean": 0,
        "quality": 0.9,
    }
    base.update(kwargs)
    with pytest.raises(ValidationError):
        ThermalGridMessage(**base)  # type: ignore[arg-type]


def test_features_centroid_optional_and_range_checked() -> None:
    absent = ThermalFeaturesMessage(
        ts=1.0, presence=False, presence_confidence=0.0, warm_region_area=0.0
    )
    assert absent.warm_region_centroid is None
    with pytest.raises(ValidationError):
        ThermalFeaturesMessage(
            ts=1.0,
            presence=True,
            presence_confidence=0.5,
            warm_region_area=0.1,
            warm_region_centroid=[1.5, 0.2],  # outside the unit square
        )
