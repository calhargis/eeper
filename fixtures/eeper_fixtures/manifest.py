"""Fixture manifest schema + validation (M2.0 criterion 1).

The manifest is the single source of truth: every source clip is one entry with a
stable fetch reference, a SHA-256, a license from the allowed (non-NC) set, labels,
and a human verification status. No audio is committed; the manifest + a
deterministic build reproduce the library. ``validate`` is what CI runs — it fails
on any missing field or a disallowed (e.g. NC) license.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# License policy (spec §M2.0): NC-licensed sources (ESC-50, CC-BY-NC, Sampling+) are
# EXCLUDED. Only permissive / share-alike-with-attribution licenses are allowed.
ALLOWED_LICENSES = frozenset(
    {
        "CC0-1.0",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "ODbL-1.0",
        "Apache-2.0",
        "PD",  # public domain / US-Gov
    }
)

ROLES = frozenset({"fg_cry", "bg_floor", "confuser", "rir"})
SPLITS = frozenset({"eval", "dev"})
VERIFICATION_STATES = frozenset({"verified", "pending"})
FETCH_MODES = frozenset({"direct", "archive-member"})
GEN_KINDS = frozenset({"white_noise", "lullaby", "nursery_floor"})

# Confuser categories present in fixtures-v1 (the source-able set). sibling_other is
# deferred to v1.1 pending project-recorded child-speech clips (tracked in PROGRESS).
CONFUSER_CATEGORIES = frozenset({"speech", "music_tv", "pets", "whitenoise_lullaby"})
# The full category set the statistical floor may reference (v1.1 adds sibling_other).
ALL_CONFUSER_CATEGORIES = CONFUSER_CATEGORIES | {"sibling_other"}


class ManifestError(ValueError):
    """The manifest is malformed or violates policy."""


@dataclass(frozen=True)
class Fetch:
    mode: str
    url: str
    member_path: str | None = None
    archive_sha256: str | None = None


@dataclass(frozen=True)
class Gen:
    """Deterministic local synthesis spec for a CC0 clip we generate ourselves
    (white noise, a simple lullaby melody, the nursery noise floor) — no fetch."""

    kind: str  # white_noise | lullaby | nursery_floor
    seed: int
    duration: float
    level: float = 0.1


@dataclass(frozen=True)
class ClipSpec:
    clip_id: str
    source: str
    fetch: Fetch
    sha256: str  # of the fetched clip (direct) or the extracted member (archive-member)
    license: str
    attribution: str
    labels: tuple[str, ...]
    role: str
    split: str
    verification_status: str
    category: str | None = None  # required when role == "confuser"
    generated: bool = False  # synthesized locally (white-noise/lullaby); no fetch
    gen: Gen | None = None  # required when generated is True


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    fixture_version: str
    clips: tuple[ClipSpec, ...] = field(default_factory=tuple)


def _clip_from_dict(raw: dict[str, Any]) -> ClipSpec:
    fetch_raw = raw.get("fetch") or {}
    gen_raw = raw.get("gen")
    gen = (
        Gen(
            kind=gen_raw.get("kind", ""),
            seed=int(gen_raw.get("seed", 0)),
            duration=float(gen_raw.get("duration", 0.0)),
            level=float(gen_raw.get("level", 0.1)),
        )
        if gen_raw
        else None
    )
    return ClipSpec(
        clip_id=raw.get("clip_id", ""),
        source=raw.get("source", ""),
        fetch=Fetch(
            mode=fetch_raw.get("mode", ""),
            url=fetch_raw.get("url", ""),
            member_path=fetch_raw.get("member_path"),
            archive_sha256=fetch_raw.get("archive_sha256"),
        ),
        sha256=raw.get("sha256", "").lower(),
        license=raw.get("license", ""),
        attribution=raw.get("attribution", ""),
        labels=tuple(raw.get("labels", ())),
        role=raw.get("role", ""),
        split=raw.get("split", ""),
        verification_status=raw.get("verification_status", ""),
        category=raw.get("category"),
        generated=bool(raw.get("generated", False)),
        gen=gen,
    )


def load_manifest(path: Path) -> Manifest:
    data = json.loads(Path(path).read_text())
    return Manifest(
        schema_version=int(data.get("schema_version", 0)),
        fixture_version=data.get("fixture_version", ""),
        clips=tuple(_clip_from_dict(c) for c in data.get("clips", [])),
    )


def validate(manifest: Manifest) -> list[str]:
    """Return a list of human-readable errors (empty == valid). CI fails if non-empty."""
    errors: list[str] = []
    if manifest.schema_version != 1:
        errors.append(f"schema_version must be 1, got {manifest.schema_version!r}")
    if not manifest.fixture_version:
        errors.append("fixture_version is required")
    seen: set[str] = set()
    for i, clip in enumerate(manifest.clips):
        where = f"clip[{i}] {clip.clip_id or '<no id>'}"
        if not clip.clip_id:
            errors.append(f"{where}: missing clip_id")
        elif clip.clip_id in seen:
            errors.append(f"{where}: duplicate clip_id")
        else:
            seen.add(clip.clip_id)
        for name, value in (
            ("source", clip.source),
            ("sha256", clip.sha256),
            ("license", clip.license),
            ("attribution", clip.attribution),
            ("role", clip.role),
            ("split", clip.split),
            ("verification_status", clip.verification_status),
        ):
            if not value:
                errors.append(f"{where}: missing {name}")
        if not clip.labels:
            errors.append(f"{where}: missing labels")
        if clip.license and clip.license not in ALLOWED_LICENSES:
            errors.append(f"{where}: disallowed license {clip.license!r} (NC or unknown)")
        if clip.role and clip.role not in ROLES:
            errors.append(f"{where}: invalid role {clip.role!r}")
        if clip.split and clip.split not in SPLITS:
            errors.append(f"{where}: invalid split {clip.split!r}")
        if clip.verification_status and clip.verification_status not in VERIFICATION_STATES:
            errors.append(f"{where}: invalid verification_status {clip.verification_status!r}")
        if clip.role == "confuser" and clip.category not in ALL_CONFUSER_CATEGORIES:
            errors.append(f"{where}: confuser needs a valid category, got {clip.category!r}")
        # A generated clip is synthesized locally (needs a gen spec); every other
        # clip has an external fetch.
        if clip.generated:
            if clip.gen is None or clip.gen.kind not in GEN_KINDS:
                errors.append(f"{where}: generated clip needs a valid gen.kind {sorted(GEN_KINDS)}")
        else:
            if clip.fetch.mode not in FETCH_MODES:
                errors.append(f"{where}: invalid fetch.mode {clip.fetch.mode!r}")
            if not clip.fetch.url:
                errors.append(f"{where}: missing fetch.url")
            if clip.fetch.mode == "archive-member" and not clip.fetch.member_path:
                errors.append(f"{where}: archive-member fetch needs member_path")
    return errors
