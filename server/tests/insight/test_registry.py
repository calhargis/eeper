"""C5: the extractor registry reports exactly the extractors matching inputs."""

from __future__ import annotations

from eeper.insight.registry import REGISTRY, Modality, available_inputs, extractors_for


def _names(*, has_audio: bool) -> set[str]:
    return {e.name for e in extractors_for(available_inputs(has_audio))}


def test_video_only_camera_reports_exactly_motion() -> None:
    assert _names(has_audio=False) == {"motion"}


def test_camera_with_audio_reports_motion_sound_level_and_cry() -> None:
    # Both audio extractors match an audio camera's inputs; cry is "available" but
    # experimental (its off-by-default gate is a runtime setting, not a modality one).
    assert _names(has_audio=True) == {"motion", "sound_level", "cry"}


def test_extractor_statuses_and_requirements() -> None:
    by_name = {e.name: e for e in REGISTRY}
    assert by_name["motion"].status == "active"
    assert by_name["sound_level"].status == "active"
    assert by_name["cry"].status == "experimental"
    assert by_name["motion"].requires == frozenset({Modality.VIDEO})
    assert by_name["sound_level"].requires == frozenset({Modality.AUDIO})
    assert by_name["cry"].requires == frozenset({Modality.AUDIO})


def test_extractors_are_sorted_by_name() -> None:
    names = [e.name for e in extractors_for(available_inputs(has_audio=True))]
    assert names == sorted(names)
