"""Sustained sound-level detection — eeper's v1 audio nudge (M2.3).

This is the robust, model-free heart of every classic audio baby monitor: in a quiet
nursery, sustained sound above the ambient floor means the baby needs attention. It
makes no claim to tell a cry from a bark or a loud TV — that is cry *classification*,
which pretrained models can't yet carry to a first-class bar (experimental in
:mod:`cry`; the trained model is M2.5). What it does do is dead reliable: a crying
baby is unambiguously louder than the room's quiet floor, so this fires ~always,
within seconds, with essentially no false alarms on a quiet night.

Design:

* **Loudness** is per-window RMS in dBFS — level-relative, so it works across mic
  gains without calibration.
* **Baseline** is a slow EWMA of the loudness, updated only while the room is quiet
  (frozen during an elevated stretch so a long cry isn't absorbed into its own
  baseline). This adapts to a white-noise machine or a slowly changing floor over
  minutes without chasing a sudden onset.
* **Elevation** = loudness - baseline. A window "votes" elevated when elevation clears
  the sensitivity-derived margin. k-of-n voting + a refractory period (the hysteresis
  discipline the motion and cry detectors share) turns a *sustained* rise into one
  ``sound_elevated`` event and ignores a transient door-slam.

An awareness signal — "sustained sound in the nursery" — never a medical readout.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from eeper.insight import frontend

_SILENCE_DBFS = -120.0


def window_loudness_dbfs(waveform: npt.NDArray[np.float32]) -> float:
    """RMS loudness of a window in dBFS (0 dBFS = full scale). Digital silence (and an
    empty window) floors at -120 dBFS rather than -inf."""
    if waveform.size == 0:
        return _SILENCE_DBFS
    rms = float(np.sqrt(np.mean(np.asarray(waveform, dtype=np.float64) ** 2)))
    if rms <= 1e-6:
        return _SILENCE_DBFS
    return 20.0 * math.log10(rms)


def loudness_from_pcm(pcm: bytes) -> float:
    return window_loudness_dbfs(frontend.pcm_to_waveform(pcm))


# ── sensitivity -> elevation margin ────────────────────────────────────────────
# The margin (dB above the adaptive baseline) a window must clear to vote "elevated".
# Sensitivity is a 0..1 knob: higher => smaller margin => more sensitive. Defaults
# calibrated on the fixtures long-form episodes/nights (models/cryeval.py): a real
# sustained cry sits ~8-18 dB over a quiet floor, so the default catches it with
# margin while ignoring floor fluctuation.
DEFAULT_SENSITIVITY = 0.5
_MARGIN_AT_MIN_SENS = 12.0  # sensitivity 0.0 (least sensitive)
_MARGIN_AT_MAX_SENS = 4.0  # sensitivity 1.0 (most sensitive); default 0.5 -> 8 dB


def margin_for(sensitivity: float) -> float:
    """Map a 0..1 sensitivity knob to an elevation margin in dB (linear across the
    operating range)."""
    s = min(1.0, max(0.0, sensitivity))
    return _MARGIN_AT_MIN_SENS + (_MARGIN_AT_MAX_SENS - _MARGIN_AT_MIN_SENS) * s


# k-of-n sustained-elevation voting + baseline adaptation (one window ~= 1 s).
SUSTAIN_WINDOW = 5  # n
SUSTAIN_COUNT = 3  # k: >= k of the last n windows elevated -> sound event
REFRACTORY_WINDOWS = 30  # min windows between sound events (one spell = one nudge)
BASELINE_ALPHA = 0.02  # slow EWMA (~50-window / ~1 min time constant), quiet-only
HYSTERESIS_DB = 3.0  # exit margin is (margin - hysteresis), so exit lags entry


@dataclass(frozen=True)
class SoundLevelConfig:
    margin_db: float
    sustain_window: int = SUSTAIN_WINDOW
    sustain_count: int = SUSTAIN_COUNT
    refractory_windows: int = REFRACTORY_WINDOWS
    baseline_alpha: float = BASELINE_ALPHA
    hysteresis_db: float = HYSTERESIS_DB


def config_for(sensitivity: float) -> SoundLevelConfig:
    return SoundLevelConfig(margin_db=margin_for(sensitivity))


class SoundLevelDetector:
    """Adaptive-baseline sustained-loudness detector. Emits ``"elevated"`` on the
    rising edge of a sustained sound and ``"quiet"`` on the falling edge.

    Baseline is seeded on the first window and updated by a slow EWMA only while
    quiet, so it tracks ambient drift (a white-noise machine, HVAC) without absorbing
    a genuine sound. :meth:`revert` undoes a transition whose durable write failed, so
    the DB and the published state never diverge (the motion/cry contract)."""

    def __init__(self, config: SoundLevelConfig, initial: str = "quiet") -> None:
        self._cfg = config
        self.state = initial
        self._baseline: float | None = None
        self._votes: deque[int] = deque(maxlen=config.sustain_window)
        self._tick = 0
        self._last_event_tick: int | None = None

    @property
    def baseline_dbfs(self) -> float | None:
        return self._baseline

    @property
    def last_event_tick(self) -> int | None:
        return self._last_event_tick

    def snapshot(self) -> tuple[str, float | None, list[int], int | None]:
        return self.state, self._baseline, list(self._votes), self._last_event_tick

    def revert(
        self, state: str, baseline: float | None, votes: list[int], last_event_tick: int | None
    ) -> None:
        self.state = state
        self._baseline = baseline
        self._votes = deque(votes, maxlen=self._cfg.sustain_window)
        self._last_event_tick = last_event_tick

    def update(self, loudness_dbfs: float) -> str | None:
        """Feed one window's loudness (called ~once per second). Returns the new state
        on a transition, else ``None``."""
        self._tick += 1
        if self._baseline is None:
            self._baseline = loudness_dbfs  # seed on the first window (a quiet lead-in)
        elevation = loudness_dbfs - self._baseline
        # Exit uses a lower margin than entry (hysteresis): once elevated, stay until
        # the sound clearly subsides.
        entry_margin = self._cfg.margin_db
        exit_margin = self._cfg.margin_db - self._cfg.hysteresis_db
        threshold = exit_margin if self.state == "elevated" else entry_margin
        vote = 1 if elevation >= threshold else 0
        self._votes.append(vote)
        elevated = sum(self._votes) >= self._cfg.sustain_count

        # Adapt the baseline only on a genuinely quiet window — one that is itself below
        # the margin (vote == 0) — while the machine is quiet. Adapting on a window that
        # already voted elevated would pull the baseline up toward a sustained
        # near-margin sound during the k-of-n vote-accumulation phase and absorb it
        # before it could ever trip; the baseline is frozen entirely while elevated.
        if self.state == "quiet" and vote == 0:
            a = self._cfg.baseline_alpha
            self._baseline = (1 - a) * self._baseline + a * loudness_dbfs

        if self.state == "quiet" and elevated:
            last = self._last_event_tick
            if last is not None and (self._tick - last) < self._cfg.refractory_windows:
                return None  # too soon after the last event — hold (anti-repeat-nudge)
            self.state = "elevated"
            self._last_event_tick = self._tick
            return "elevated"
        if self.state == "elevated" and not elevated:
            self.state = "quiet"
            return "quiet"
        return None
