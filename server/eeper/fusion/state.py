"""The fusion state machine (M3.3): per-epoch features → sleep/wake + calm/distressed.

Streaming by design — feed one :class:`EpochFeatures` as each epoch closes and it
returns that epoch's :class:`EpochState`. The same object drives the offline replay
(feed a whole night) and the live engine (feed as epochs land), so CI validates the
exact code that runs in production. Pure stdlib; no numpy (must run on a Pi).
"""

from __future__ import annotations

import statistics
from collections import deque

from eeper.fusion.model import (
    DEFAULT_PARAMS,
    Arousal,
    EpochFeatures,
    EpochState,
    FusionParams,
    Sleep,
)

# Activity = mean of the available movement/sound signals. Presence and cry are NOT
# activity inputs: presence is context (is anyone there) and cry feeds distress only.
_ACTIVITY_FIELDS = ("motion", "radar_move", "sound")


class FusionStateMachine:
    """Sleep/wake via a median-smoothed activity score through a hysteresis band with
    a post-transition sustain count; calm/distressed via multi-signal corroboration.

    The median smoother rejects isolated single-epoch spikes (a sleep twitch) that a
    per-epoch threshold would misread as a wake, while preserving a sustained level (a
    real wake) — this is the lift a naive classifier lacks. The sustain then confirms a
    transition only after it persists, and the wide enter/exit band kills flapping.
    """

    def __init__(self, params: FusionParams = DEFAULT_PARAMS, initial: Sleep = Sleep.WAKE) -> None:
        self._p = params
        self.sleep = initial
        self._recent: deque[float] = deque(maxlen=max(1, params.smooth_window))
        self._run_active = 0
        self._run_quiet = 0

    @staticmethod
    def activity(f: EpochFeatures) -> float:
        """Mean of the present activity signals; absent modalities are skipped so the
        score is valid under any input subset (0.0 only when nothing is live)."""
        vals = [v for name in _ACTIVITY_FIELDS if (v := getattr(f, name)) is not None]
        return statistics.fmean(vals) if vals else 0.0

    def _corroborators(self, f: EpochFeatures) -> tuple[str, ...]:
        """The signals currently over their distress thresholds (each an independent
        vote). Absent modalities simply can't vote."""
        p = self._p
        votes: list[str] = []
        if f.cry is not None and f.cry >= p.cry_threshold:
            votes.append("cry")
        if f.sound is not None and f.sound >= p.sound_threshold:
            votes.append("sound")
        if f.radar_move is not None and f.radar_move >= p.radar_threshold:
            votes.append("radar")
        if f.motion is not None and f.motion >= p.motion_threshold:
            votes.append("motion")
        return tuple(votes)

    def _confidence(self, activity: float) -> float:
        """0..1 distance of the activity from the nearer hysteresis edge, normalized by
        the band width — how decisively this epoch sits in its state."""
        p = self._p
        band = max(1e-6, p.act_wake - p.act_sleep)
        if self.sleep is Sleep.SLEEP:
            return max(0.0, min(1.0, (p.act_wake - activity) / band))
        return max(0.0, min(1.0, (activity - p.act_sleep) / band))

    def update(self, f: EpochFeatures) -> EpochState:
        p = self._p
        self._recent.append(self.activity(f))
        smoothed = statistics.median(self._recent)

        if smoothed >= p.act_wake:
            self._run_active += 1
            self._run_quiet = 0
        elif smoothed <= p.act_sleep:
            self._run_quiet += 1
            self._run_active = 0
        else:  # inside the hysteresis band — hold, reset both runs
            self._run_active = 0
            self._run_quiet = 0

        if self.sleep is Sleep.SLEEP and self._run_active >= p.wake_sustain:
            self.sleep = Sleep.WAKE
        elif self.sleep is Sleep.WAKE and self._run_quiet >= p.sleep_sustain:
            self.sleep = Sleep.SLEEP

        votes = self._corroborators(f)
        distressed = self.sleep is Sleep.WAKE and len(votes) >= p.min_corroborators
        arousal = Arousal.DISTRESSED if distressed else Arousal.CALM
        # Provenance: the extractors behind this state — the corroborators when
        # distressed, else whatever modalities were present this epoch.
        inputs = votes if distressed else f.inputs
        return EpochState(
            sleep=self.sleep,
            arousal=arousal,
            activity=smoothed,
            confidence=self._confidence(smoothed),
            inputs=inputs,
        )


def run(
    features: list[EpochFeatures],
    params: FusionParams = DEFAULT_PARAMS,
    initial: Sleep = Sleep.WAKE,
) -> list[EpochState]:
    """Replay a full night of epoch features through a fresh state machine."""
    sm = FusionStateMachine(params, initial)
    return [sm.update(f) for f in features]
