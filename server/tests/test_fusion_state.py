"""Unit tests for the fusion state machine internals (M3.3)."""

from __future__ import annotations

from eeper.fusion.model import EpochFeatures, FusionParams, Sleep
from eeper.fusion.state import FusionStateMachine, run

_P = FusionParams()


def _activity_stream(values: list[float]) -> list[EpochFeatures]:
    # motion-only features so activity == the given value.
    return [EpochFeatures(motion=v) for v in values]


def test_activity_skips_absent_modalities() -> None:
    assert FusionStateMachine.activity(EpochFeatures(motion=0.4, radar_move=0.6)) == 0.5
    assert FusionStateMachine.activity(EpochFeatures(sound=0.3)) == 0.3
    # presence/cry are not activity inputs; all-absent scores 0.
    assert FusionStateMachine.activity(EpochFeatures(presence=1.0, cry=1.0)) == 0.0
    assert FusionStateMachine.activity(EpochFeatures()) == 0.0


def test_isolated_spike_is_rejected_by_the_smoother() -> None:
    # Quiet with one lone high epoch: the median smoother keeps it asleep.
    stream = _activity_stream([0.05] * 8 + [0.9] + [0.05] * 8)
    states = run(stream, _P, initial=Sleep.SLEEP)
    assert all(s.sleep is Sleep.SLEEP for s in states), "a 1-epoch spike flipped the state"


def test_sustained_activity_confirms_a_wake() -> None:
    states = run(_activity_stream([0.9] * 5), _P, initial=Sleep.SLEEP)
    # Confirmed after wake_sustain epochs of activity, not before.
    assert states[_P.wake_sustain - 1].sleep is Sleep.WAKE
    assert states[0].sleep is Sleep.SLEEP


def test_sustained_quiet_confirms_sleep_onset() -> None:
    states = run(_activity_stream([0.02] * 10), _P, initial=Sleep.WAKE)
    assert states[_P.sleep_sustain - 1].sleep is Sleep.SLEEP
    assert states[0].sleep is Sleep.WAKE


def test_hysteresis_band_holds_the_state() -> None:
    # Activity parked between the exit and enter thresholds never transitions.
    mid = (_P.act_sleep + _P.act_wake) / 2
    assert run(_activity_stream([mid] * 20), _P, initial=Sleep.SLEEP)[-1].sleep is Sleep.SLEEP
    assert run(_activity_stream([mid] * 20), _P, initial=Sleep.WAKE)[-1].sleep is Sleep.WAKE


def test_confidence_stays_in_unit_range() -> None:
    states = run(_activity_stream([0.0, 0.5, 1.0, 0.2, 0.8]), _P)
    assert all(0.0 <= s.confidence <= 1.0 for s in states)
