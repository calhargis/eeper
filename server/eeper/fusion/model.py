"""Fusion value types + tunable parameters (M3.3).

Deliberately non-clinical vocabulary: sleep/wake and calm/distressed are awareness
states, never medical or vital-sign readouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

EPOCH_SECONDS = 30  # sleep-study convention; one fused state per 30 s bin


class Sleep(StrEnum):
    SLEEP = "sleep"
    WAKE = "wake"


class Arousal(StrEnum):
    CALM = "calm"
    DISTRESSED = "distressed"


@dataclass(frozen=True)
class EpochFeatures:
    """Aggregated signals for one epoch. Each field is ``None`` when that modality
    contributed no sample to the epoch (sensor offline, camera down, audio disabled),
    so the fusion degrades gracefully to whatever inputs are live. Values are 0..1.

    ``motion`` = camera movement intensity; ``radar_move`` = mmWave/PIR movement;
    ``presence`` = occupancy (0/1); ``sound`` = audio level; ``cry`` = cry indicator
    (0/1). ``inputs`` lists the extractor names that contributed (for provenance)."""

    motion: float | None = None
    radar_move: float | None = None
    presence: float | None = None
    sound: float | None = None
    cry: float | None = None
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpochState:
    """The fused result for one epoch."""

    sleep: Sleep
    arousal: Arousal
    activity: float  # the smoothed 0..1 activity score behind the decision
    confidence: float  # 0..1, distance of the activity from the nearer hysteresis edge
    inputs: tuple[str, ...] = ()  # extractors that corroborated this state


@dataclass(frozen=True)
class SleepSession:
    """A consolidated sleep period: fell-asleep → woke, in epoch indices (half-open
    ``[start, end)``). Brief intra-sleep awakenings shorter than the consolidation
    break do not split a session."""

    start_epoch: int
    end_epoch: int

    @property
    def length_epochs(self) -> int:
        return self.end_epoch - self.start_epoch


@dataclass(frozen=True)
class FusionParams:
    """Tunable fusion thresholds. Defaults calibrated against the synthetic replay
    suite (M3.3 de-risk): they clear the epoch-agreement floor with margin on every
    modality subset while a naive per-epoch threshold does not.

    Sleep/wake runs on a median-smoothed activity score (spike rejection) through a
    hysteresis band with a post-transition sustain count, mirroring the M2.2 movement
    state machine: a wide band kills flapping, the sustain confirms a real transition.
    ``wake_sustain`` is deliberately below the 3-minute wake floor so every wake that
    long is caught."""

    act_wake: float = 0.28  # sleep→wake enter (smoothed activity ≥ this)
    act_sleep: float = 0.18  # wake→sleep exit (smoothed activity ≤ this)
    wake_sustain: int = 2  # epochs of activity to confirm a wake (< 3 min)
    sleep_sustain: int = 6  # epochs of quiet (3 min) to confirm sleep onset
    smooth_window: int = 3  # causal median window — rejects isolated 1-epoch spikes

    # Distress needs ≥ min_corroborators of these signals over their thresholds, and
    # only while awake — so a single loud noise or lone motion spike never distresses.
    cry_threshold: float = 0.5
    sound_threshold: float = 0.6
    radar_threshold: float = 0.6
    motion_threshold: float = 0.6
    min_corroborators: int = 2

    # A wake this long (epochs) ends a sleep session; shorter awakenings stay within it.
    session_break_epochs: int = 20  # 10 min at 30 s epochs


DEFAULT_PARAMS = FusionParams()
