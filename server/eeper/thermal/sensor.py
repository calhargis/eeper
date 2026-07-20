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


class _MlxDriver(Protocol):
    def getFrame(self, buf: list[float]) -> object: ...  # noqa: N802 — vendor API name


# The §4.5 contract's valid per-cell range. A real MLX90640 emits the occasional
# out-of-range "dead" pixel (a defective/uncomputable cell reads ~ -273.15 °C); the
# datasheet allows a few. We repair those from their neighbours so one bad cell doesn't
# invalidate the whole grid — but treat a large fraction as a genuinely corrupt read.
_T_LO, _T_HI = -40.0, 300.0
_MAX_DEAD = 16  # ~2% of 768; beyond this the frame is corrupt, not merely blemished


def repair_dead_pixels(
    grid: list[float], *, lo: float = _T_LO, hi: float = _T_HI, max_dead: int = _MAX_DEAD
) -> list[float] | None:
    """Replace out-of-range (dead/stuck) cells with the mean of their in-range neighbours,
    so a lone defective pixel doesn't fail §4.5 validation. Returns the repaired grid, the
    same list when nothing is wrong, or ``None`` when too many cells are bad (corrupt read)."""
    bad = [i for i, v in enumerate(grid) if not math.isfinite(v) or not (lo <= v <= hi)]
    if not bad:
        return grid
    if len(bad) > max_dead:
        return None
    badset = set(bad)
    good = [v for i, v in enumerate(grid) if i not in badset]
    fallback = sorted(good)[len(good) // 2] if good else 0.0  # frame median
    out = list(grid)
    for i in bad:
        r, c = divmod(i, COLS)
        neigh = [
            grid[rr * COLS + cc]
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            if (dr or dc)
            and 0 <= (rr := r + dr) < ROWS
            and 0 <= (cc := c + dc) < COLS
            and (rr * COLS + cc) not in badset
        ]
        out[i] = sum(neigh) / len(neigh) if neigh else fallback
    return out


class MlxThermalSensor:
    """A :class:`ThermalSensor` backed by a real MLX90640 over I²C.

    The vendor driver fills a 768-value buffer in place and raises on a checksum / read
    failure — which we turn into ``None`` so the publisher degrades health instead of
    emitting a bad grid. Construct with any object exposing ``getFrame(buf)`` (so the
    adapter is testable without hardware); use :meth:`open` for the real device, which
    lazily imports the CircuitPython stack so the base image never needs it.
    """

    def __init__(self, driver: _MlxDriver) -> None:
        self._driver = driver
        self._buf = [0.0] * (ROWS * COLS)

    @classmethod
    def open(cls, *, bus: int = 1, i2c_frequency: int = 800_000) -> MlxThermalSensor:  # pragma: no cover
        # Hardware only — imported lazily (needs the `thermal` extra + a Pi with I²C).
        # Open the I²C bus by NUMBER (``/dev/i2c-{bus}``) via ExtendedI2C rather than through
        # ``board``: the MLX90640 needs only I²C, and ``import board`` would drag in the GPIO
        # pin stack (board auto-detect + RPi.GPIO), which is fragile off bare metal — notably
        # inside a container, where board detection fails and the GPIO libs can't reach the
        # hardware. Talking to the bus directly keeps the node deployment-agnostic.
        import adafruit_mlx90640  # type: ignore[import-not-found]
        from adafruit_extended_bus import ExtendedI2C  # type: ignore[import-not-found]

        i2c = ExtendedI2C(bus, frequency=i2c_frequency)
        mlx = adafruit_mlx90640.MLX90640(i2c)
        mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
        return cls(mlx)

    def read(self) -> list[float] | None:
        try:
            self._driver.getFrame(self._buf)
        except (ValueError, RuntimeError, OSError):
            return None  # checksum / I²C read failure → the publisher degrades health
        # Repair the sensor's occasional dead pixels; a mostly-bad frame reads as a
        # failed read (None) so the publisher degrades health rather than emit garbage.
        return repair_dead_pixels(list(self._buf))  # snapshot; the driver reuses its buffer
