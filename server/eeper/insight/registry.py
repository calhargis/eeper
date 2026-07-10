"""Feature-extractor registry (M2.2).

Each extractor declares which input modalities it needs; an extractor is
"available" for a camera when every modality it requires is present. Video is
always present — camera motion needs no extra hardware, the graceful-degradation
principle — while audio depends on the source carrying an audio track. This is how
the same engine serves every hardware combination: it instantiates exactly the
extractors matching a camera's declared inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Modality(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


@dataclass(frozen=True)
class ExtractorSpec:
    name: str
    requires: frozenset[Modality]
    # "active": implemented and running by default. "experimental": implemented but
    # off by default because it can't yet meet its quality bar (the cry classifier — see
    # models/cryeval.py; M2.5's de-risk showed a trained model can't lift it on the
    # current corpus, so first-class cry is gated on the M2.6 corpus expansion).
    # "declared": the input branch is recognised but the extractor lands later.
    status: str


REGISTRY: tuple[ExtractorSpec, ...] = (
    ExtractorSpec("motion", frozenset({Modality.VIDEO}), "active"),
    # Sound level (sustained loudness above the nursery floor) is the v1 audio nudge —
    # robust, model-free, always on for audio cameras.
    ExtractorSpec("sound_level", frozenset({Modality.AUDIO}), "active"),
    # Cry classification is experimental + off by default (pretrained YAMNet can't carry
    # it to the bar; M2.5's de-risk showed a trained model can't either on the current
    # corpus — first-class cry is gated on the M2.6 corpus expansion).
    ExtractorSpec("cry", frozenset({Modality.AUDIO}), "experimental"),
)


def available_inputs(has_audio: bool) -> frozenset[Modality]:
    """The modalities eeper can derive from a camera. Video is always available;
    audio only when the source carries an audio track (``has_audio``)."""
    inputs = {Modality.VIDEO}
    if has_audio:
        inputs.add(Modality.AUDIO)
    return frozenset(inputs)


def extractors_for(inputs: frozenset[Modality]) -> tuple[ExtractorSpec, ...]:
    """The extractors whose required modalities are all available, sorted by name.
    "Matching available inputs" == ``extractor.requires`` is a subset of ``inputs``.
    """
    return tuple(sorted((e for e in REGISTRY if e.requires <= inputs), key=lambda e: e.name))
