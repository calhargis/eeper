"""Replay harness + scoring (M3.3): run a fixture night through the real fusion
pipeline (featurize → state machine) and score the prediction against its labels.

The same featurize→run path the live engine uses, so the CI quality gate exercises
production code, not a parallel implementation.
"""

from __future__ import annotations

from collections.abc import Sequence

from eeper.fusion.epochs import featurize
from eeper.fusion.model import DEFAULT_PARAMS, EPOCH_SECONDS, EpochState, FusionParams, Sleep
from eeper.fusion.state import run
from eeper.fusion.synth import FixtureNight, mask_to_subset


def replay(
    night: FixtureNight, modalities: set[str], params: FusionParams = DEFAULT_PARAMS
) -> list[EpochState]:
    """Mask the night to an input subset, featurize onto the epoch grid, and fuse."""
    samples = mask_to_subset(night, modalities)
    features = featurize(samples, night.start, night.n_epochs, EPOCH_SECONDS)
    return run(features, params)


def sleep_agreement(gt: Sequence[Sleep], pred: Sequence[EpochState]) -> float:
    """Fraction of epochs whose predicted sleep/wake matches the label."""
    ok = sum(1 for g, p in zip(gt, pred, strict=True) if g is p.sleep)
    return ok / len(pred) if pred else 0.0


def wake_recall(
    wakes: Sequence[tuple[int, int]], pred: Sequence[EpochState], min_epochs: int
) -> tuple[int, int]:
    """Of ground-truth wakes at least ``min_epochs`` long, how many overlap a predicted
    wake epoch. Returns ``(detected, total)``."""
    detected = total = 0
    for start, end in wakes:
        if end - start < min_epochs:
            continue
        total += 1
        if any(pred[i].sleep is Sleep.WAKE for i in range(start, end)):
            detected += 1
    return detected, total
