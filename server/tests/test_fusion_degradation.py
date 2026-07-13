"""M3.3 combinatorial-degradation gate (crit 2): the fusion runs on every input
subset and always produces valid, defined states — accuracy may degrade, but it must
never crash or emit an undefined sleep/arousal.
"""

from __future__ import annotations

from itertools import combinations

import pytest

from eeper.fusion.model import Arousal, Sleep
from eeper.fusion.replay import replay
from eeper.fusion.synth import generate

_MODS = ("video", "radar", "audio")
# Every subset, including the empty set (all inputs down).
_ALL_SUBSETS = [set(c) for r in range(len(_MODS) + 1) for c in combinations(_MODS, r)]


@pytest.mark.parametrize("mods", _ALL_SUBSETS, ids=lambda m: "+".join(sorted(m)) or "none")
@pytest.mark.parametrize("seed", [0, 1, 7])
def test_every_subset_yields_only_valid_states(mods: set[str], seed: int) -> None:
    night = generate(seed)
    states = replay(night, mods)
    assert len(states) == night.n_epochs  # one state per epoch, always
    for st in states:
        assert st.sleep in (Sleep.SLEEP, Sleep.WAKE)
        assert st.arousal in (Arousal.CALM, Arousal.DISTRESSED)
        assert 0.0 <= st.activity <= 1.0
        assert 0.0 <= st.confidence <= 1.0


def test_all_inputs_down_does_not_crash_and_stays_calm() -> None:
    # No modalities: activity is 0 everywhere → it settles to sleep, never distressed
    # (distress needs corroborators, which need live inputs).
    states = replay(generate(3), set())
    assert all(s.arousal is Arousal.CALM for s in states)
    assert all(s.activity == 0.0 for s in states)


def test_dropping_a_modality_still_scores_from_the_rest() -> None:
    # radar+audio (no video) must still classify — not fall back to a single fixed state.
    states = replay(generate(5), {"radar", "audio"})
    seen = {s.sleep for s in states}
    assert seen == {Sleep.SLEEP, Sleep.WAKE}  # both states occur across the night
