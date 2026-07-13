"""Fusion layer (M3.3): derive sleep/wake and calm/distressed states from the
per-epoch outputs of every registered extractor (camera motion, mmWave/PIR movement
and presence, sound level, cry), plus the sleep-session records they bound.

Pure, deterministic, dependency-light (stdlib only) so it runs on a Pi in the live
engine AND replays labeled fixture nights offline in CI. These are awareness signals
— sleep/wake and calm/distressed — never a medical, diagnostic, or vital-sign readout.
"""

from eeper.fusion.model import (
    Arousal,
    EpochFeatures,
    EpochState,
    FusionParams,
    Sleep,
    SleepSession,
)
from eeper.fusion.sessions import (
    backdate_transitions,
    extract_sessions,
    sessions_from_prediction,
)
from eeper.fusion.state import FusionStateMachine

__all__ = [
    "Arousal",
    "EpochFeatures",
    "EpochState",
    "FusionParams",
    "FusionStateMachine",
    "Sleep",
    "SleepSession",
    "backdate_transitions",
    "extract_sessions",
    "sessions_from_prediction",
]
