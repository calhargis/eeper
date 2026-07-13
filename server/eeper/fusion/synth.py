"""Seeded synthetic night generator (M3.3).

Produces labeled full-night fixture traces — a ground-truth sleep/wake + calm/distressed
epoch sequence rendered into NOISY, sub-epoch, multi-modal samples (camera motion,
mmWave/PIR movement + presence, sound, cry). The replay gate asserts the fusion recovers
the labels; real-world accuracy stays the M3.3 [MANUAL] overnight-bench criterion.

Deterministic (stdlib ``random``, seeded) so CI is reproducible. The difficulty is
calibrated (from the de-risk) so a principled fusion clears the floors with margin while
a naive per-epoch threshold does not: sleep carries isolated *visual* twitches a smoother
rejects, and real wakes are distinguished by sustained activity rather than amplitude
alone. NOT a model of any real infant — a stress test for the fusion logic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from eeper.fusion.epochs import Sample
from eeper.fusion.model import EPOCH_SECONDS, Arousal, Sleep

# Modality → the fields it carries; used to mask a night down to an input subset.
MODALITY_FIELDS = {
    "video": ("motion",),
    "radar": ("radar_move", "presence"),
    "audio": ("sound", "cry"),
}
_FIELD_SOURCE = {
    "motion": "camera",
    "radar_move": "sensor",
    "presence": "sensor",
    "sound": "audio",
    "cry": "audio",
}


@dataclass(frozen=True)
class FixtureNight:
    """A labeled synthetic night: ground-truth per-epoch labels + the raw samples a
    node/camera would have emitted, plus the wake episodes for recall scoring."""

    seed: int
    start: float
    n_epochs: int
    gt_sleep: list[Sleep]
    gt_arousal: list[Arousal]
    wakes: list[tuple[int, int]]  # half-open [start, end) epoch indices
    samples: list[Sample]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mark_wake(
    sleep: list[Sleep], arousal: list[Arousal], start: int, end: int, distressed: bool
) -> None:
    for i in range(start, end):
        sleep[i] = Sleep.WAKE
        arousal[i] = Arousal.DISTRESSED if distressed else Arousal.CALM


def _ground_truth(
    rng: random.Random, n: int
) -> tuple[list[Sleep], list[Arousal], list[tuple[int, int]]]:
    """A night of 1–3 consolidated sleep sessions separated by clearly-long awake gaps
    (well above the session break), each containing brief/medium intra-session arousals
    (well below the break). Keeping wake durations away from the break threshold makes
    the session COUNT robust to small epoch errors, while the mix of ≥3-min and <3-min
    arousals exercises the wake-recall floor."""
    sleep = [Sleep.WAKE] * n
    arousal = [Arousal.CALM] * n
    wakes: list[tuple[int, int]] = []

    onset = rng.randint(10, 30)  # settle to sleep after 5–15 min
    morning = rng.randint(n - 30, n - 5)  # final morning wake
    n_sessions = rng.randint(1, 3)
    inter_gaps = [rng.randint(28, 46) for _ in range(n_sessions - 1)]  # >> break (20)
    sleep_budget = morning - onset - sum(inter_gaps)
    # Random split of the sleep budget into per-session block lengths.
    cuts = sorted(rng.randint(0, sleep_budget) for _ in range(n_sessions - 1))
    blocks = [b - a for a, b in zip([0, *cuts], [*cuts, sleep_budget], strict=True)]

    cursor = onset
    for si, blen in enumerate(blocks):
        b_start, b_end = cursor, cursor + blen
        for i in range(b_start, b_end):
            sleep[i] = Sleep.SLEEP
        # Intra-session arousals: brief stirs (<3 min) or medium (3–6 min), never long
        # enough to split the session (max 12 < break 20).
        for _ in range(rng.randint(0, 3)):
            if blen < 30:
                break
            a_start = rng.randint(b_start + 3, b_end - 14)
            a_dur = rng.randint(1, 5) if rng.random() < 0.5 else rng.randint(6, 12)
            a_end = min(a_start + a_dur, b_end - 2)
            _mark_wake(sleep, arousal, a_start, a_end, rng.random() < 0.4)
            wakes.append((a_start, a_end))
        cursor = b_end
        if si < len(blocks) - 1:  # long inter-session awake gap
            gap = inter_gaps[si]
            _mark_wake(sleep, arousal, cursor, cursor + gap, rng.random() < 0.3)
            wakes.append((cursor, cursor + gap))
            cursor += gap

    _mark_wake(sleep, arousal, morning, n, rng.random() < 0.3)
    wakes.append((morning, n))
    return sleep, arousal, sorted(wakes)


def _emit(
    rng: random.Random,
    samples: list[Sample],
    t0: float,
    field_: str,
    mean: float,
    sd: float,
    offsets: tuple[int, ...],
) -> None:
    for off in offsets:
        samples.append(
            Sample(
                ts=t0 + off,
                field=field_,
                value=_clamp(rng.gauss(mean, sd)),
                source=_FIELD_SOURCE[field_],
            )
        )


def generate(seed: int, hours: int = 10, epoch_seconds: int = EPOCH_SECONDS) -> FixtureNight:
    n = hours * 3600 // epoch_seconds
    gt_rng = random.Random(seed)
    sleep, arousal, wakes = _ground_truth(gt_rng, n)

    rng = random.Random(seed ^ 0x5EED)
    samples: list[Sample] = []
    prev_spike = False
    for i in range(n):
        t0 = float(i * epoch_seconds)
        if sleep[i] is Sleep.SLEEP:
            # ~13% of sleep epochs carry an ISOLATED visual twitch; radar stays in-band.
            spike = not prev_spike and rng.random() < 0.13
            if spike:
                _emit(rng, samples, t0, "motion", 0.55, 0.12, (5, 15, 25))
                _emit(rng, samples, t0, "radar_move", 0.24, 0.08, (7, 22))
            else:
                _emit(rng, samples, t0, "motion", 0.08, 0.06, (5, 15, 25))
                _emit(rng, samples, t0, "radar_move", 0.12, 0.07, (7, 22))
            _emit(rng, samples, t0, "sound", 0.10, 0.06, (5, 15, 25))
            prev_spike = spike
        elif arousal[i] is Arousal.CALM:  # awake, calm — overlapping amplitude, sustained
            _emit(rng, samples, t0, "motion", 0.38, 0.11, (5, 15, 25))
            _emit(rng, samples, t0, "radar_move", 0.44, 0.11, (7, 22))
            _emit(rng, samples, t0, "sound", 0.32, 0.12, (5, 15, 25))
            prev_spike = False
        else:  # awake, distressed
            _emit(rng, samples, t0, "motion", 0.68, 0.15, (5, 15, 25))
            _emit(rng, samples, t0, "radar_move", 0.72, 0.15, (7, 22))
            _emit(rng, samples, t0, "sound", 0.72, 0.15, (5, 15, 25))
            if rng.random() < 0.8:
                samples.append(Sample(ts=t0 + 3, field="cry", value=1.0, source="audio"))
            prev_spike = False
        # Presence: occupied all night (the crib is occupied); reported by the radar.
        _emit(rng, samples, t0, "presence", 1.0, 0.0, (7, 22))

    return FixtureNight(
        seed=seed,
        start=0.0,
        n_epochs=n,
        gt_sleep=sleep,
        gt_arousal=arousal,
        wakes=wakes,
        samples=samples,
    )


def mask_to_subset(night: FixtureNight, modalities: set[str]) -> list[Sample]:
    """Keep only the samples whose field belongs to an active modality — the input for
    the combinatorial-degradation subsets (video-only, radar-only, video+audio, all)."""
    keep = {f for m in modalities for f in MODALITY_FIELDS.get(m, ())}
    return [s for s in night.samples if s.field in keep]
