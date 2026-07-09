"""Unit tests for the (experimental) cry classifier scoring + episode detector logic
(M2.3). The ONNX session itself is covered by the multi-arch smoke test and the
quality gate; here we test the pure scoring + k-of-n voting."""

from __future__ import annotations

import numpy as np

from eeper.insight import cry


def _scores(cry_val: float, animal_val: float, patches: int = 3) -> np.ndarray:
    s = np.zeros((patches, 521), np.float32)
    for idx in cry.CRY_CLASS_INDICES:
        s[:, idx] = cry_val
    for idx in cry.ANIMAL_CLASS_INDICES:
        s[:, idx] = animal_val
    return s


def test_window_score_sums_cry_band() -> None:
    # Four cry classes at 0.2 each, no animal -> peak band 0.8.
    assert abs(cry.window_score(_scores(0.2, 0.0)) - 0.8) < 1e-5
    assert cry.window_score(np.zeros((0, 521), np.float32)) == 0.0


def test_animal_band_suppresses_pet_confusers() -> None:
    # A pet that lights the cry band AND the animal band scores far lower than a clean
    # cry with the same cry-band activation.
    clean = cry.window_score(_scores(0.2, 0.0))
    pet = cry.window_score(_scores(0.2, 0.5))
    assert pet < clean
    assert abs(pet - (0.8 - cry.ANIMAL_SUPPRESS * 0.5)) < 1e-5


def test_window_threshold_monotonic_in_sensitivity() -> None:
    assert (
        cry.window_threshold_for(0.0)
        > cry.window_threshold_for(0.5)
        > cry.window_threshold_for(1.0)
    )
    assert abs(cry.window_threshold_for(0.0) - cry._THRESHOLD_AT_MIN_SENS) < 1e-9
    assert abs(cry.window_threshold_for(1.0) - cry._THRESHOLD_AT_MAX_SENS) < 1e-9


def _det(refractory_windows: int = 20) -> cry.CryEpisodeDetector:
    return cry.CryEpisodeDetector(
        cry.CryDetectorConfig(
            threshold=0.05, vote_window=5, vote_count=3, refractory_windows=refractory_windows
        )
    )


def test_k_of_n_voting_needs_a_sustained_signal() -> None:
    det = _det()
    # Fewer than 3 of the last 5 above threshold does not fire.
    assert det.update(0.9) is None  # votes 1
    assert det.update(0.0) is None  # 1,0
    assert det.update(0.9) is None  # 1,0,1 -> sum 2
    assert det.update(0.0) is None  # 1,0,1,0 -> sum 2
    # The third above-threshold window within the last five -> crying.
    assert det.update(0.9) == "crying"  # 1,0,1,0,1 -> sum 3 >= 3


def test_falling_edge_returns_to_quiet() -> None:
    det = _det()
    for _ in range(3):
        det.update(0.9)
    assert det.state == "crying"
    outs = [det.update(0.0) for _ in range(5)]
    assert "quiet" in outs
    assert det.state == "quiet"


def test_refractory_blocks_a_second_onset() -> None:
    det = _det(refractory_windows=10)
    for _ in range(3):
        det.update(0.9)  # onset at tick 3
    for _ in range(3):
        det.update(0.0)  # back to quiet
    # Another sustained burst immediately: within the refractory window -> no re-onset.
    second = [det.update(0.9) for _ in range(3)]
    assert "crying" not in second


def test_revert_restores_state() -> None:
    det = _det()
    snap = det.snapshot()
    for _ in range(3):
        det.update(0.9)
    assert det.state == "crying"
    det.revert(*snap)
    assert det.state == "quiet"
    assert det.last_onset_tick == snap[2]
