"""C1: the motion score ranks still < rolling < sitting_up (ordering, not values)."""

from __future__ import annotations

from eeper.insight.motion import DEFAULT_THRESHOLDS, frame_diff_score
from tests.insight.gen_fixtures import fixture


def _sequence_score(frames: list[bytes]) -> float:
    return sum(frame_diff_score(a, b) for a, b in zip(frames, frames[1:], strict=False)) / (
        len(frames) - 1
    )


def test_motion_score_ranks_still_below_rolling_below_sitting_up() -> None:
    still = _sequence_score(fixture("still"))
    rolling = _sequence_score(fixture("rolling"))
    sitting = _sequence_score(fixture("sitting_up"))
    assert still < rolling < sitting, f"{still=} {rolling=} {sitting=}"


def test_fixture_scores_bracket_the_state_thresholds() -> None:
    # Calibration guard: still is (near) zero, rolling clears the medium-enter
    # threshold, sitting_up clears the high-enter threshold — so the thresholds sit
    # in the gaps between fixture levels. If a base-image ffmpeg/decoder change ever
    # shifts the scores, this fails loudly rather than silently miscalibrating.
    t = DEFAULT_THRESHOLDS
    assert _sequence_score(fixture("still")) < t.lm_dn
    assert _sequence_score(fixture("rolling")) > t.lm_up
    assert _sequence_score(fixture("sitting_up")) > t.mh_up
