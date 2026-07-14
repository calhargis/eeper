"""M6.1 thermal feature extractor: an empty room reports no presence; a warm body is
detected with a centroid near it; a bigger body yields more area + confidence. This pins
the extractor's DIRECTION and determinism — its accuracy is the separate M6.2 gate."""

from __future__ import annotations

import random

import pytest

from eeper.thermal.features import CELLS, COLS, ROWS, derive_features
from eeper.thermal.sensor import Scene, WarmBlob, render


def test_empty_room_no_presence() -> None:
    grid = render(Scene(ambient_c=21.0), random.Random(1))
    f = derive_features(grid)
    assert f.presence is False
    assert f.warm_region_centroid is None
    assert f.presence_confidence == 0.0


def test_warm_body_detected_with_centroid_near_it() -> None:
    blob = WarmBlob(row=12.0, col=16.0, radius=3.0, delta_c=8.0)
    grid = render(Scene(ambient_c=21.0, blobs=(blob,)), random.Random(2))
    f = derive_features(grid)
    assert f.presence is True
    assert 0.0 < f.presence_confidence <= 1.0
    assert f.warm_region_centroid is not None
    row, col = f.warm_region_centroid
    assert abs(row - 12.0 / (ROWS - 1)) < 0.1
    assert abs(col - 16.0 / (COLS - 1)) < 0.1


def test_bigger_body_more_area_and_confidence() -> None:
    small = derive_features(
        render(Scene(blobs=(WarmBlob(12.0, 16.0, 2.0, 8.0),)), random.Random(3))
    )
    big = derive_features(render(Scene(blobs=(WarmBlob(12.0, 16.0, 5.0, 8.0),)), random.Random(3)))
    assert big.warm_region_area > small.warm_region_area
    assert big.presence_confidence >= small.presence_confidence


def test_render_is_reproducible_and_full_size() -> None:
    a = render(Scene(), random.Random(7))
    b = render(Scene(), random.Random(7))
    assert a == b and len(a) == CELLS


def test_wrong_length_raises() -> None:
    with pytest.raises(ValueError, match="cells"):
        derive_features([21.0] * 10)
