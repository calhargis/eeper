"""M3.3 corroboration gate (crit 3): a distress state requires at least two
corroborating signals — a single loud noise or lone motion spike must never distress —
and distress is only ever emitted while awake.
"""

from __future__ import annotations

from eeper.fusion.model import Arousal, EpochFeatures, Sleep
from eeper.fusion.state import FusionStateMachine


def _awake_machine() -> FusionStateMachine:
    """A machine driven firmly awake (sustained high activity) so we can probe the
    arousal decision in the awake state."""
    sm = FusionStateMachine(initial=Sleep.WAKE)
    for _ in range(4):
        sm.update(EpochFeatures(motion=0.9, radar_move=0.9, sound=0.9, cry=1.0))
    return sm


def test_single_strong_signal_is_not_distress() -> None:
    sm = _awake_machine()
    # Only sound over its threshold; motion/radar low. Still awake, but one corroborator.
    st = sm.update(EpochFeatures(motion=0.2, radar_move=0.2, sound=0.9, cry=0.0))
    assert st.sleep is Sleep.WAKE
    assert st.arousal is Arousal.CALM


def test_two_corroborating_signals_are_distress() -> None:
    sm = _awake_machine()
    st = sm.update(EpochFeatures(motion=0.2, radar_move=0.9, sound=0.9, cry=0.0))
    assert st.sleep is Sleep.WAKE
    assert st.arousal is Arousal.DISTRESSED
    assert set(st.inputs) >= {"radar", "sound"}  # provenance names the corroborators


def test_cry_alone_is_not_distress() -> None:
    sm = _awake_machine()
    st = sm.update(EpochFeatures(motion=0.1, radar_move=0.1, sound=0.1, cry=1.0))
    assert st.arousal is Arousal.CALM  # one signal (cry) is not enough


def test_a_single_live_input_can_never_distress() -> None:
    # Only audio present. Even a maxed-out cry+sound is a single modality's word — but
    # cry AND sound are two independent signals, so THAT corroborates. A lone signal
    # (sound only) from one modality does not.
    sm = FusionStateMachine(initial=Sleep.WAKE)
    for _ in range(4):
        sm.update(EpochFeatures(sound=0.9))  # drive awake on audio alone
    lone = sm.update(EpochFeatures(sound=0.9))
    assert lone.arousal is Arousal.CALM


def test_distress_never_while_asleep() -> None:
    sm = FusionStateMachine(initial=Sleep.SLEEP)
    # A brief cry+noise blip during sleep that isn't sustained enough to confirm a wake
    # must not surface as distress (still asleep this epoch).
    st = sm.update(EpochFeatures(motion=0.1, radar_move=0.1, sound=0.9, cry=1.0))
    assert st.sleep is Sleep.SLEEP
    assert st.arousal is Arousal.CALM
