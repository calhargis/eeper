"""C2: hysteresis + min-dwell — an oscillation around a threshold does not flap."""

from __future__ import annotations

import math

from eeper.insight.motion import DEFAULT_THRESHOLDS, Ewma, Level, MovementStateMachine


def _run(scores: list[float], dt: float = 0.2) -> list[Level]:
    """Feed raw scores through the EWMA + state machine at ``dt`` spacing (5 Hz);
    return the emitted transitions."""
    ewma = Ewma()
    sm = MovementStateMachine()
    changes: list[Level] = []
    now = 0.0
    for raw in scores:
        transition = sm.update(ewma.update(raw), now)
        if transition is not None:
            changes.append(transition)
        now += dt
    return changes


def test_oscillation_around_threshold_yields_at_most_one_change() -> None:
    # A raw trace oscillating around the low->medium enter threshold with amplitude
    # 0.003 (range [lm_up-0.003, lm_up+0.003]). Even the EWMA trough stays well above
    # the medium->low exit line (lm_dn=0.003), so the trace can cross UP once and can
    # never satisfy the down-threshold: <= 1 change, no flapping.
    t = DEFAULT_THRESHOLDS
    mean, amp = t.lm_up, 0.003
    # phase so the first sample sits at the trough -> unambiguously LOW to start
    trace = [mean + amp * math.sin(2 * math.pi * i / 8 - math.pi / 2) for i in range(200)]
    changes = _run(trace)
    assert len(changes) <= 1, f"flapping: {changes}"


def test_onset_from_quiet_transitions_on_the_first_crossing() -> None:
    # C3 unit half: a step from quiet (0.0) to strong motion fires a change off LOW
    # immediately (leading edge has no dwell delay).
    changes = _run([0.0, 0.0, 0.0, 0.06, 0.06, 0.06, 0.06, 0.06])
    assert changes and changes[0] in (Level.MEDIUM, Level.HIGH)


def test_min_dwell_blocks_a_rapid_second_flip() -> None:
    sm = MovementStateMachine()
    assert sm.update(0.02, 0.0) is Level.MEDIUM  # first transition: instant
    assert sm.update(0.06, 0.1) is None  # 0.1s later a HIGH target is dwell-blocked
    assert sm.update(0.06, 0.7) is Level.HIGH  # after the 0.6s dwell, allowed


def test_still_score_stays_low() -> None:
    changes = _run([0.0] * 20)
    assert changes == []


def test_revert_restores_state_so_a_failed_write_retries() -> None:
    # When a transition's durable write fails, the supervisor reverts the machine so
    # a later tick re-attempts it (no permanent DB/MQTT divergence). Reverting must
    # restore both the level and the dwell clock, so the retry is not dwell-blocked.
    sm = MovementStateMachine()
    before, before_change = sm.level, sm.last_change_monotonic
    assert sm.update(0.02, 0.0) is Level.MEDIUM
    sm.revert(before, before_change)
    assert sm.level is Level.LOW
    assert sm.last_change_monotonic is None
    # The next tick re-emits the transition rather than staying silently advanced.
    assert sm.update(0.02, 0.1) is Level.MEDIUM
