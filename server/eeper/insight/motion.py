"""Camera motion score + movement-level state machine (M2.2).

Motion is a normalized mean-absolute difference between consecutive gray frames
(pure Python, no numpy — cheap enough for a Pi). An EWMA smooths single-frame
spikes, and a three-level hysteresis state machine derives the movement level.

This is an awareness signal — movement level, never a medical or vital-sign
reading. The vocabulary here is deliberately limited to low/medium/high.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def frame_diff_score(prev: bytes, cur: bytes) -> float:
    """Normalized mean absolute difference between two equal-length gray frames,
    in [0, 1]. 0.0 = identical frames; larger = more inter-frame motion. Returns
    0.0 for empty or mismatched-length input (a defensive no-op, never raises)."""
    n = len(cur)
    if n == 0 or len(prev) != n:
        return 0.0
    total = sum(abs(a - b) for a, b in zip(prev, cur, strict=False))
    return total / (n * 255)


class Ewma:
    """Exponential moving average. ``alpha=0.5`` gives a ~1-frame half-life: fast
    enough to register a real onset within the C3 budget, yet enough damping that a
    single-frame spike can't by itself flip the movement level."""

    def __init__(self, alpha: float = 0.5) -> None:
        self._alpha = alpha
        self.value = 0.0
        self._seeded = False

    def update(self, sample: float) -> float:
        if not self._seeded:
            self.value = sample  # seed with the first observation
            self._seeded = True
        else:
            self.value = self._alpha * sample + (1.0 - self._alpha) * self.value
        return self.value


class Level(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Thresholds:
    """Dual enter/exit thresholds (the hysteresis band) on the smoothed score.
    Calibrated against the measured fixtures: still ~0.0, a rolling block ~0.012,
    a sitting-up block ~0.052, camera testsrc2 motion ~0.012. The bands are set
    well inside those gaps for CI headroom."""

    lm_up: float = 0.008  # low -> medium (enter)
    lm_dn: float = 0.003  # medium -> low (exit)
    mh_up: float = 0.040  # medium -> high (enter)
    mh_dn: float = 0.025  # high -> medium (exit)


DEFAULT_THRESHOLDS = Thresholds()
MIN_DWELL_SECONDS = 0.6  # 3 frames at 5 fps


def confidence_for(score: float, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> float:
    """A deterministic 0..1 confidence: how far the score has climbed toward the
    top (high) threshold, clamped. Monotone in the score, non-clinical."""
    return max(0.0, min(1.0, score / thresholds.mh_up))


class MovementStateMachine:
    """Three-level (low/medium/high) movement state with hysteresis + min-dwell.

    Anti-flap (C2) and fast onset (C3) are reconciled by putting the two on
    different edges: the wide hysteresis BAND requires a large swing to reverse a
    level (killing flapping), while the min-dwell is POST-transition only, so the
    FIRST transition after a quiet stretch has zero delay (a genuine onset registers
    on the first crossing). The dwell only blocks a rapid *second* flip.
    """

    def __init__(
        self,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        min_dwell_seconds: float = MIN_DWELL_SECONDS,
        initial: Level = Level.LOW,
    ) -> None:
        self._t = thresholds
        self._min_dwell = min_dwell_seconds
        self.level = initial
        self._last_change_monotonic: float | None = None

    @property
    def last_change_monotonic(self) -> float | None:
        return self._last_change_monotonic

    def revert(self, level: Level, last_change_monotonic: float | None) -> None:
        """Undo the most recent transition — used when its durable write failed, so
        a later tick re-attempts it instead of leaving the DB and the published
        state permanently diverged (the in-memory transition is not the source of
        truth until it is persisted)."""
        self.level = level
        self._last_change_monotonic = last_change_monotonic

    def _target(self, score: float) -> Level:
        """The level the score points to, respecting the hysteresis band around the
        current level (one step at a time)."""
        t = self._t
        if self.level is Level.LOW:
            return Level.MEDIUM if score >= t.lm_up else Level.LOW
        if self.level is Level.MEDIUM:
            if score >= t.mh_up:
                return Level.HIGH
            if score <= t.lm_dn:
                return Level.LOW
            return Level.MEDIUM
        # HIGH
        return Level.MEDIUM if score <= t.mh_dn else Level.HIGH

    def update(self, score: float, now: float) -> Level | None:
        """Feed a (smoothed) score at monotonic time ``now``. Returns the new
        :class:`Level` on a transition, else ``None``."""
        target = self._target(score)
        if target is self.level:
            return None
        last = self._last_change_monotonic
        if last is not None and (now - last) < self._min_dwell:
            return None  # too soon after the last change — hold (anti-flap guard)
        self.level = target
        self._last_change_monotonic = now
        return target
