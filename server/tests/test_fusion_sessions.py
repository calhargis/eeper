"""M3.3 session-integrity gate (crit 4a) + session-extraction unit tests.

The replayed night must reproduce the labeled number of sleep sessions with boundaries
within ±2 min. As with the replay gate, the aggregate floors sit under the measured
envelope (80 seeds: session-count match 0.963, boundary-in-tolerance 0.892–0.968).
"""

from __future__ import annotations

import pytest

from eeper.fusion.model import EPOCH_SECONDS, FusionParams, Sleep
from eeper.fusion.replay import replay
from eeper.fusion.sessions import backdate_transitions, extract_sessions, sessions_from_prediction
from eeper.fusion.synth import generate

SUBSETS: dict[str, set[str]] = {
    "all": {"video", "radar", "audio"},
    "video-only": {"video"},
    "radar-only": {"radar"},
    "video+audio": {"video", "audio"},
}
SEEDS = range(60)
BOUNDARY_TOL = (2 * 60) // EPOCH_SECONDS  # 4 epochs = ±2 min

SESSION_COUNT_FLOOR = 0.90  # measured 0.963
BOUNDARY_TOL_FLOOR = 0.85  # measured 0.892 (radar-only) … 0.968


@pytest.mark.parametrize("label", SUBSETS)
def test_session_count_and_boundaries(label: str) -> None:
    mods = SUBSETS[label]
    count_match = 0
    in_tol = boundaries = 0
    for s in SEEDS:
        night = generate(s)
        gt = extract_sessions(night.gt_sleep)
        pred = sessions_from_prediction(replay(night, mods))
        if len(gt) == len(pred):
            count_match += 1
            for g, p in zip(gt, pred, strict=True):
                for ge, pe in ((g.start_epoch, p.start_epoch), (g.end_epoch, p.end_epoch)):
                    boundaries += 1
                    in_tol += abs(ge - pe) <= BOUNDARY_TOL
    n = len(list(SEEDS))
    assert count_match / n >= SESSION_COUNT_FLOOR, f"{label}: session-count match {count_match}/{n}"
    assert boundaries > 0
    assert in_tol / boundaries >= BOUNDARY_TOL_FLOOR, (
        f"{label}: boundaries in ±2min {in_tol}/{boundaries}"
    )


# ── unit tests: the pure session logic ────────────────────────────────────────

_P = FusionParams()  # session_break_epochs = 20
S, W = Sleep.SLEEP, Sleep.WAKE


def test_brief_awakening_does_not_split_a_session() -> None:
    # sleep, a 3-epoch stir (< break), sleep — one consolidated session.
    timeline = [S] * 30 + [W] * 3 + [S] * 30
    sessions = extract_sessions(timeline, _P)
    assert len(sessions) == 1
    assert (sessions[0].start_epoch, sessions[0].end_epoch) == (0, 63)


def test_long_awakening_splits_into_two_sessions() -> None:
    # a 25-epoch gap (> break) separates two sessions.
    timeline = [S] * 30 + [W] * 25 + [S] * 30
    sessions = extract_sessions(timeline, _P)
    assert len(sessions) == 2
    assert (sessions[0].start_epoch, sessions[0].end_epoch) == (0, 30)
    assert (sessions[1].start_epoch, sessions[1].end_epoch) == (55, 85)


def test_no_sleep_yields_no_sessions() -> None:
    assert extract_sessions([W] * 50, _P) == []


def test_backdating_moves_a_confirmed_onset_earlier() -> None:
    # A wake→sleep transition confirmed at epoch 20 actually began sleep_sustain epochs
    # earlier (the quiet run start); back-dating recovers that.
    timeline = [W] * 20 + [S] * 20
    corrected = backdate_transitions(timeline, _P)
    # Onset moves from 20 back to 20-(sleep_sustain-1).
    onset = corrected.index(S)
    assert onset == 20 - (_P.sleep_sustain - 1)
    # Back-dating never reorders: everything after the onset is still sleep.
    assert all(x is S for x in corrected[onset:])
