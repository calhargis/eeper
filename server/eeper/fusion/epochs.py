"""Epoch featurization (M3.3): raw timestamped signal samples → per-epoch
:class:`EpochFeatures`, the input the state machine consumes.

Each modality reports at its own irregular cadence (camera motion on transitions,
sensor readings ~every 15 s, cry as discrete events). This bins them onto the shared
30-second epoch grid. A modality that contributed no sample to an epoch is left
``None`` for that epoch — so a sensor dropping out, or replaying a video-only subset,
degrades to the live inputs instead of fabricating zeros.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from eeper.fusion.model import EPOCH_SECONDS, EpochFeatures

# The continuous activity signals are averaged over the epoch; the binary-ish
# indicators take the max (occupied/crying if it happened at all in the window).
_MEAN_FIELDS = ("motion", "radar_move", "sound")
_MAX_FIELDS = ("presence", "cry")
_FIELDS = _MEAN_FIELDS + _MAX_FIELDS


@dataclass(frozen=True)
class Sample:
    """One reading from one modality. ``field`` is an :class:`EpochFeatures` field
    name; ``source`` is the extractor that produced it (for provenance)."""

    ts: float  # seconds
    field: str
    value: float
    source: str = ""


def featurize(
    samples: Iterable[Sample],
    start: float,
    n_epochs: int,
    epoch_seconds: int = EPOCH_SECONDS,
) -> list[EpochFeatures]:
    """Bin ``samples`` onto ``n_epochs`` epochs starting at ``start`` (seconds).

    Samples outside ``[start, start + n_epochs*epoch_seconds)`` are ignored; an unknown
    ``field`` is skipped (never raises — a malformed row must not stall fusion)."""
    # Per-epoch accumulators: field -> list of values, plus contributing sources.
    buckets: list[dict[str, list[float]]] = [{} for _ in range(n_epochs)]
    sources: list[set[str]] = [set() for _ in range(n_epochs)]
    span = n_epochs * epoch_seconds
    for s in samples:
        if s.field not in _FIELDS or not (start <= s.ts < start + span):
            continue
        idx = int((s.ts - start) // epoch_seconds)
        buckets[idx].setdefault(s.field, []).append(s.value)
        if s.source:
            sources[idx].add(s.source)

    out: list[EpochFeatures] = []
    for bucket, srcs in zip(buckets, sources, strict=True):
        agg: dict[str, float | None] = {}
        for field in _MEAN_FIELDS:
            vals = bucket.get(field)
            agg[field] = sum(vals) / len(vals) if vals else None
        for field in _MAX_FIELDS:
            vals = bucket.get(field)
            agg[field] = max(vals) if vals else None
        out.append(
            EpochFeatures(
                motion=agg["motion"],
                radar_move=agg["radar_move"],
                presence=agg["presence"],
                sound=agg["sound"],
                cry=agg["cry"],
                inputs=tuple(sorted(srcs)),
            )
        )
    return out
