"""Fixture library gates (M2.0 criteria 4, 5, 6).

These run against the built library's scene index (``scenes.json``, emitted by the
build) and the manifest. Each returns a list of human-readable errors (empty ==
pass); CI fails the build if any is non-empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from eeper_fixtures.manifest import CONFUSER_CATEGORIES, Manifest

# Statistical floor (spec §M2.0), on the frozen EVAL split.
MIN_CRY_SCENES = 100
MIN_CONFUSER_SCENES = 300
MIN_PER_CONFUSER_CATEGORY = 30
# Minimum DISTINCT foreground source clips behind each category's eval scenes — so
# the floor can't be met by multiplying 1-2 clips into many near-duplicate scenes.
MIN_DISTINCT_SOURCES = 8


@dataclass(frozen=True)
class Scene:
    scene_id: str
    split: str
    is_cry: bool
    category: str | None  # confuser category; None for a cry scene
    duration: float
    events: tuple[tuple[float, float, str], ...]  # (onset, offset, label)
    source_clip_ids: tuple[str, ...]
    fg_source_clip_ids: tuple[str, ...] = ()  # the foreground event's source(s)


def load_scenes(index_path: Path) -> list[Scene]:
    data = json.loads(Path(index_path).read_text())
    return [
        Scene(
            scene_id=s["scene_id"],
            split=s["split"],
            is_cry=bool(s["is_cry"]),
            category=s.get("category"),
            duration=float(s["duration"]),
            events=tuple((float(o), float(f), lab) for o, f, lab in s["events"]),
            source_clip_ids=tuple(s.get("source_clip_ids", ())),
            fg_source_clip_ids=tuple(s.get("fg_source_clip_ids", ())),
        )
        for s in data["scenes"]
    ]


def check_splits(manifest: Manifest) -> list[str]:
    """Criterion 4: no SOURCE clip (identified by its content sha256) contributes to
    both the eval and dev splits."""
    splits_by_sha: dict[str, set[str]] = {}
    for clip in manifest.clips:
        splits_by_sha.setdefault(clip.sha256, set()).add(clip.split)
    errors = [
        f"source clip {sha[:12]}… appears in both splits {sorted(splits)}"
        for sha, splits in splits_by_sha.items()
        if len(splits) > 1
    ]
    return errors


def check_scene_splits(scenes: list[Scene]) -> list[str]:
    """Belt-and-suspenders for criterion 4 at the built-scene level: a source clip
    must not appear in scenes of more than one split."""
    splits_by_source: dict[str, set[str]] = {}
    for scene in scenes:
        for src in scene.source_clip_ids:
            splits_by_source.setdefault(src, set()).add(scene.split)
    return [
        f"source clip {src} used across splits {sorted(splits)}"
        for src, splits in splits_by_source.items()
        if len(splits) > 1
    ]


def check_floor(scenes: list[Scene]) -> list[str]:
    """Criterion 5: the eval split meets the scene-count floor overall and per
    (present) confuser category."""
    evals = [s for s in scenes if s.split == "eval"]
    cry = sum(1 for s in evals if s.is_cry)
    confusers = [s for s in evals if not s.is_cry]
    errors: list[str] = []
    if cry < MIN_CRY_SCENES:
        errors.append(f"eval cry scenes {cry} < {MIN_CRY_SCENES}")
    if len(confusers) < MIN_CONFUSER_SCENES:
        errors.append(f"eval confuser scenes {len(confusers)} < {MIN_CONFUSER_SCENES}")
    per_category: dict[str, int] = dict.fromkeys(CONFUSER_CATEGORIES, 0)
    distinct: dict[str, set[str]] = {c: set() for c in CONFUSER_CATEGORIES}
    for scene in confusers:
        if scene.category in per_category:
            per_category[scene.category] += 1
            distinct[scene.category].update(scene.fg_source_clip_ids)
    for category, count in sorted(per_category.items()):
        if count < MIN_PER_CONFUSER_CATEGORY:
            errors.append(
                f"eval confuser category {category!r} {count} < {MIN_PER_CONFUSER_CATEGORY}"
            )
        if len(distinct[category]) < MIN_DISTINCT_SOURCES:
            errors.append(
                f"eval confuser category {category!r} has {len(distinct[category])} distinct "
                f"source clips < {MIN_DISTINCT_SOURCES} (scenes would be near-duplicates)"
            )
    cry_sources: set[str] = set()
    for scene in evals:
        if scene.is_cry:
            cry_sources.update(scene.fg_source_clip_ids)
    if len(cry_sources) < MIN_DISTINCT_SOURCES:
        errors.append(
            f"eval cry scenes have {len(cry_sources)} distinct sources < {MIN_DISTINCT_SOURCES}"
        )
    return errors


def check_annotations(scenes: list[Scene]) -> list[str]:
    """Criterion 6: every event's onset/offset lies within the scene bounds, and
    every cry scene has at least one cry event."""
    errors: list[str] = []
    for scene in scenes:
        for onset, offset, label in scene.events:
            if not (0.0 <= onset <= offset <= scene.duration + 1e-6):
                errors.append(
                    f"{scene.scene_id}: event {label!r} ({onset:.3f},{offset:.3f}) "
                    f"out of bounds [0,{scene.duration:.3f}]"
                )
        if scene.is_cry and not any(lab == "cry" for _, _, lab in scene.events):
            errors.append(f"{scene.scene_id}: cry scene has no cry event")
    return errors
