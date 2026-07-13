"""Sleep-session extraction (M3.3): a sleep/wake epoch timeline → consolidated
sleep sessions (fell-asleep → woke boundaries).

A session is a maximal run of sleep in which brief awakenings — shorter than the
consolidation break — are bridged, so a 90-second stir doesn't shatter one night's
sleep into a dozen "sessions". A wake at least ``session_break_epochs`` long ends the
session. Pure function of the timeline, so it runs identically on the fused prediction
and on a fixture's ground-truth labels (the M3.3 session-integrity gate compares the
two).
"""

from __future__ import annotations

from collections.abc import Sequence

from eeper.fusion.model import DEFAULT_PARAMS, EpochState, FusionParams, Sleep, SleepSession


def backdate_transitions(
    sleep_states: Sequence[Sleep], params: FusionParams = DEFAULT_PARAMS
) -> list[Sleep]:
    """Undo the state machine's confirmation delay for boundary reporting.

    A transition is confirmed only after its sustaining run persists (``wake_sustain`` /
    ``sleep_sustain`` epochs), so the confirmed timeline lags the true transition by that
    much. For accurate session *boundaries* (the ±2-min gate) we back-date each confirmed
    transition to when its run began — the honest transition time — clamped so a
    back-dated boundary can never cross the previous one. Steady-state epoch agreement is
    scored on the un-corrected live timeline; this correction is only for event timing."""
    seq = list(sleep_states)
    n = len(seq)
    if n == 0:
        return seq
    # Parallel lists of confirmed transitions: the epoch each was confirmed at (which we
    # back-date in place) and the state it moved to.
    epochs: list[int] = []
    to_state: list[Sleep] = []
    for i in range(1, n):
        if seq[i] is not seq[i - 1]:
            back = (params.sleep_sustain if seq[i] is Sleep.SLEEP else params.wake_sustain) - 1
            epochs.append(max(0, i - back))
            to_state.append(seq[i])
    for j in range(1, len(epochs)):  # keep boundaries strictly ordered after back-dating
        if epochs[j] <= epochs[j - 1]:
            epochs[j] = epochs[j - 1] + 1

    out = [seq[0]] * n
    cur = seq[0]
    ti = 0
    for i in range(n):
        while ti < len(epochs) and epochs[ti] == i:
            cur = to_state[ti]
            ti += 1
        out[i] = cur
    return out


def sessions_from_prediction(
    states: Sequence[EpochState], params: FusionParams = DEFAULT_PARAMS
) -> list[SleepSession]:
    """Sessions from a fused prediction: back-date the confirmation lag, then extract."""
    return extract_sessions(backdate_transitions([s.sleep for s in states], params), params)


def extract_sessions(
    sleep_states: Sequence[Sleep], params: FusionParams = DEFAULT_PARAMS
) -> list[SleepSession]:
    """Bridge sub-break awakenings, then return the surviving sleep runs as sessions."""
    # 1. Raw maximal runs of SLEEP as half-open [start, end) epoch intervals.
    runs: list[list[int]] = []
    start: int | None = None
    for i, s in enumerate(sleep_states):
        if s is Sleep.SLEEP and start is None:
            start = i
        elif s is not Sleep.SLEEP and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(sleep_states)])

    # 2. Merge two runs when the wake gap between them is shorter than the break.
    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < params.session_break_epochs:
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    return [SleepSession(start_epoch=s, end_epoch=e) for s, e in merged]
