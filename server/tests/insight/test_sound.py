"""Unit tests for the sound-level detector: loudness, sensitivity mapping, and the
adaptive-baseline sustained-elevation state machine (M2.3)."""

from __future__ import annotations

import numpy as np

from eeper.insight import sound


def _cfg(margin_db: float = 8.0) -> sound.SoundLevelConfig:
    return sound.SoundLevelConfig(
        margin_db=margin_db,
        sustain_window=5,
        sustain_count=3,
        refractory_windows=30,
        baseline_alpha=0.02,
        hysteresis_db=3.0,
    )


def test_loudness_dbfs_known_levels() -> None:
    assert sound.window_loudness_dbfs(np.zeros(16000, np.float32)) == -120.0  # silence floors
    quarter = np.full(16000, 0.1, np.float32)  # RMS 0.1 -> -20 dBFS
    assert abs(sound.window_loudness_dbfs(quarter) - (-20.0)) < 0.01
    assert sound.window_loudness_dbfs(np.zeros(0, np.float32)) == -120.0  # empty is safe


def test_margin_is_monotonic_in_sensitivity() -> None:
    assert sound.margin_for(0.0) > sound.margin_for(0.5) > sound.margin_for(1.0)
    assert sound.margin_for(0.0) == sound._MARGIN_AT_MIN_SENS
    assert sound.margin_for(1.0) == sound._MARGIN_AT_MAX_SENS
    # clamped
    assert sound.margin_for(-1.0) == sound._MARGIN_AT_MIN_SENS
    assert sound.margin_for(2.0) == sound._MARGIN_AT_MAX_SENS


def test_quiet_floor_produces_no_events() -> None:
    det = sound.SoundLevelDetector(_cfg())
    events = [det.update(-44.0) for _ in range(200)]
    assert all(e is None for e in events)
    assert det.state == "quiet"


def test_sustained_elevation_fires_one_onset_then_clears() -> None:
    det = sound.SoundLevelDetector(_cfg())
    for _ in range(30):
        det.update(-44.0)  # settle the baseline on the quiet floor
    rising = [det.update(-30.0) for _ in range(6)]  # +14 dB, sustained
    assert rising.count("elevated") == 1  # exactly one onset (k-of-n, then held)
    assert det.state == "elevated"
    falling = [det.update(-44.0) for _ in range(6)]  # sound stops
    assert "quiet" in falling
    assert det.state == "quiet"


def test_sustained_near_margin_sound_fires_not_absorbed() -> None:
    # Regression: a sustained sound at exactly the entry margin (+8 dB) must fire, not
    # be absorbed into the baseline during the k-of-n vote-accumulation phase (the
    # baseline may only adapt on a window that is itself below the margin).
    det = sound.SoundLevelDetector(_cfg())
    for _ in range(30):
        det.update(-50.0)  # settle the baseline on the floor
    outs = [det.update(-42.0) for _ in range(8)]  # exactly +8 dB over the floor
    assert "elevated" in outs, "a sustained near-margin sound was absorbed, not fired"
    assert det.baseline_dbfs is not None and det.baseline_dbfs <= -49.0  # not crept up


def test_continuous_step_is_one_event_baseline_frozen_while_elevated() -> None:
    # A white-noise machine switched on: one onset, then the elevation persists (the
    # baseline is frozen while elevated, so it is not absorbed into a re-fire loop).
    det = sound.SoundLevelDetector(_cfg())
    for _ in range(30):
        det.update(-44.0)
    seq = [det.update(-30.0) for _ in range(300)]
    assert seq.count("elevated") == 1


def test_baseline_absorbs_a_slow_ambient_rise() -> None:
    # A gradually rising quiet floor (HVAC warming up) must be tracked by the baseline
    # (updated while quiet), not mistaken for a sound event.
    det = sound.SoundLevelDetector(_cfg(margin_db=8.0))
    events = [det.update(-50.0 + 0.02 * i) for i in range(300)]  # +6 dB over 5 min
    assert all(e is None for e in events)
    assert det.baseline_dbfs is not None and det.baseline_dbfs > -50.0


def test_revert_restores_pre_transition_state() -> None:
    det = sound.SoundLevelDetector(_cfg())
    for _ in range(30):
        det.update(-44.0)
    snap = det.snapshot()
    for _ in range(3):
        det.update(-30.0)
    assert det.state == "elevated"
    det.revert(*snap)
    assert det.state == "quiet"
    assert det.last_event_tick == snap[3]
