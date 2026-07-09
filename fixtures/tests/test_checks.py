"""Criteria 4, 5, 6: split disjointness, statistical floor, annotation sanity."""

from __future__ import annotations

from eeper_fixtures.checks import (
    MIN_CONFUSER_SCENES,
    MIN_CRY_SCENES,
    MIN_PER_CONFUSER_CATEGORY,
    Scene,
    check_annotations,
    check_floor,
    check_scene_splits,
    check_splits,
)
from eeper_fixtures.manifest import ClipSpec, Fetch, Manifest

_CATEGORIES = ("speech", "music_tv", "pets", "whitenoise_lullaby")


def _clip(cid: str, sha: str, split: str) -> ClipSpec:
    return ClipSpec(
        clip_id=cid,
        source="fsd50k",
        fetch=Fetch(mode="direct", url=f"https://x/{cid}.wav"),
        sha256=sha,
        license="CC0-1.0",
        attribution="x",
        labels=("speech",),
        role="confuser",
        split=split,
        verification_status="verified",
        category="speech",
    )


def test_split_disjoint_ok() -> None:
    m = Manifest(1, "fixtures-v1", (_clip("a", "1" * 64, "eval"), _clip("b", "2" * 64, "dev")))
    assert check_splits(m) == []


def test_same_source_in_both_splits_fails() -> None:
    # Same content sha256 assigned to eval AND dev — a leak at the source level.
    m = Manifest(1, "fixtures-v1", (_clip("a", "1" * 64, "eval"), _clip("b", "1" * 64, "dev")))
    assert any("both splits" in e for e in check_splits(m))


def _scenes(cry: int, per_category: dict[str, int], split: str = "eval") -> list[Scene]:
    # Each scene gets a distinct foreground source so the distinct-source floor is met.
    scenes: list[Scene] = []
    for i in range(cry):
        scenes.append(
            Scene(f"cry{i}", split, True, None, 5.0, ((1.0, 2.0, "cry"),), (f"c{i}",), (f"c{i}",))
        )
    for cat, n in per_category.items():
        for i in range(n):
            scenes.append(
                Scene(
                    f"{cat}{i}",
                    split,
                    False,
                    cat,
                    5.0,
                    ((0.0, 5.0, cat),),
                    (f"s{cat}{i}",),
                    (f"s{cat}{i}",),
                )
            )
    return scenes


def test_floor_pass() -> None:
    per = {c: max(MIN_PER_CONFUSER_CATEGORY, MIN_CONFUSER_SCENES // 4 + 1) for c in _CATEGORIES}
    scenes = _scenes(MIN_CRY_SCENES, per)
    assert check_floor(scenes) == []


def test_floor_fails_when_cry_short() -> None:
    per = dict.fromkeys(_CATEGORIES, 80)
    errors = check_floor(_scenes(MIN_CRY_SCENES - 1, per))
    assert any("cry scenes" in e for e in errors)


def test_floor_fails_when_a_category_short() -> None:
    per = dict.fromkeys(_CATEGORIES, 80)
    per["pets"] = MIN_PER_CONFUSER_CATEGORY - 1
    errors = check_floor(_scenes(MIN_CRY_SCENES, per))
    assert any("pets" in e for e in errors)


def test_floor_fails_when_sources_not_distinct() -> None:
    # Enough scenes, but a whole category built from ONE source clip (near-duplicates).
    from eeper_fixtures.checks import Scene as S

    scenes = _scenes(MIN_CRY_SCENES, dict.fromkeys(_CATEGORIES, 80))
    scenes = [s for s in scenes if s.category != "pets"]
    scenes += [
        S(f"pets{i}", "eval", False, "pets", 5.0, ((0.0, 5.0, "dog"),), ("only",), ("only",))
        for i in range(80)
    ]
    errors = check_floor(scenes)
    assert any("distinct source" in e and "pets" in e for e in errors)


def test_annotation_bounds_and_cry_event() -> None:
    good = Scene("s1", "eval", True, None, 5.0, ((1.0, 2.0, "cry"),), ("a",))
    assert check_annotations([good]) == []
    out_of_bounds = Scene("s2", "eval", False, "pets", 5.0, ((4.0, 6.0, "dog"),), ("b",))
    assert any("out of bounds" in e for e in check_annotations([out_of_bounds]))
    cry_without_event = Scene("s3", "eval", True, None, 5.0, ((0.0, 5.0, "noise"),), ("c",))
    assert any("no cry event" in e for e in check_annotations([cry_without_event]))


def test_scene_split_leak_detected() -> None:
    scenes = [
        Scene("x1", "eval", False, "pets", 5.0, ((0.0, 1.0, "dog"),), ("shared",)),
        Scene("x2", "dev", False, "pets", 5.0, ((0.0, 1.0, "dog"),), ("shared",)),
    ]
    assert any("across splits" in e for e in check_scene_splits(scenes))
