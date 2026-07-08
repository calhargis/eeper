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
    # "active": implemented and running in M2.2. "declared": the input branch is
    # recognised so the registry reports it, but the extractor itself lands later
    # (e.g. the M2.3 cry/audio-event classifier).
    status: str


REGISTRY: tuple[ExtractorSpec, ...] = (
    ExtractorSpec("motion", frozenset({Modality.VIDEO}), "active"),
    ExtractorSpec("audio_level", frozenset({Modality.AUDIO}), "declared"),
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
