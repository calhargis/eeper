"""The thermal sensor interface + a synthetic grid renderer.

:class:`ThermalSensor` is the contract the publisher reads from; ``read()`` returns a
768-value grid (°C, row-major) or ``None`` on a read failure (I²C error, checksum failure,
timeout). The real MLX90640 implementation lives behind an optional import in the node
entrypoint (M6.1 slice 2); everything here is hardware-free so the publish path and the
feature extractor can be tested and characterized without a sensor.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Protocol

from eeper.thermal.features import COLS, ROWS


class ThermalSensor(Protocol):
    def read(self) -> list[float] | None:
        """A 768-cell grid (°C, row-major), or ``None`` on a read failure."""
        ...


@dataclass(frozen=True)
class WarmBlob:
    """A warm object in the scene (a person-analog), in cell coordinates."""

    row: float
    col: float
    radius: float
    delta_c: float  # peak temperature above ambient


@dataclass(frozen=True)
class Scene:
    """A synthetic thermal scene — an ambient room with optional warm bodies. Used to
    generate realistic grids for feature tests and the M6.2 characterization scaffolding."""

    ambient_c: float = 21.0
    noise_c: float = 0.15
    blobs: tuple[WarmBlob, ...] = field(default_factory=tuple)


def render(scene: Scene, rng: random.Random) -> list[float]:
    """Render `scene` to a 768-value grid (°C, row-major). ``rng`` makes it reproducible."""
    grid: list[float] = []
    for r in range(ROWS):
        for c in range(COLS):
            t = scene.ambient_c + rng.gauss(0.0, scene.noise_c)
            for b in scene.blobs:
                dist2 = (r - b.row) ** 2 + (c - b.col) ** 2
                t += b.delta_c * math.exp(-dist2 / (2.0 * b.radius**2))
            grid.append(round(t, 2))
    return grid
