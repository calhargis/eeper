"""Criterion 1: manifest integrity — required fields + allowed-license enforcement."""

from __future__ import annotations

import json
from pathlib import Path

from eeper_fixtures.manifest import load_manifest, validate

_GOOD_CLIP = {
    "clip_id": "cry-0001",
    "source": "donateacry",
    "fetch": {"mode": "direct", "url": "https://example.test/a.wav"},
    "sha256": "a" * 64,
    "license": "ODbL-1.0",
    "attribution": "donateacry-corpus",
    "labels": ["cry"],
    "role": "fg_cry",
    "split": "eval",
    "verification_status": "verified",
}


def _write(tmp: Path, clips: list[dict], fixture_version: str = "fixtures-v1") -> Path:
    path = tmp / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": 1, "fixture_version": fixture_version, "clips": clips})
    )
    return path


def test_valid_manifest_passes(tmp_path: Path) -> None:
    confuser = {
        **_GOOD_CLIP,
        "clip_id": "spx-1",
        "source": "fsd50k",
        "license": "CC0-1.0",
        "labels": ["speech"],
        "role": "confuser",
        "category": "speech",
        "split": "dev",
    }
    assert validate(load_manifest(_write(tmp_path, [_GOOD_CLIP, confuser]))) == []


def test_missing_field_fails(tmp_path: Path) -> None:
    bad = {**_GOOD_CLIP}
    del bad["sha256"]
    errors = validate(load_manifest(_write(tmp_path, [bad])))
    assert any("missing sha256" in e for e in errors)


def test_nc_license_rejected(tmp_path: Path) -> None:
    bad = {**_GOOD_CLIP, "license": "CC-BY-NC-4.0"}
    errors = validate(load_manifest(_write(tmp_path, [bad])))
    assert any("disallowed license" in e for e in errors)


def test_confuser_without_category_fails(tmp_path: Path) -> None:
    bad = {**_GOOD_CLIP, "role": "confuser", "category": None}
    errors = validate(load_manifest(_write(tmp_path, [bad])))
    assert any("valid category" in e for e in errors)


def test_duplicate_clip_id_fails(tmp_path: Path) -> None:
    errors = validate(load_manifest(_write(tmp_path, [_GOOD_CLIP, dict(_GOOD_CLIP)])))
    assert any("duplicate clip_id" in e for e in errors)


def test_archive_member_needs_member_path(tmp_path: Path) -> None:
    bad = {**_GOOD_CLIP, "fetch": {"mode": "archive-member", "url": "https://x/a.zip"}}
    errors = validate(load_manifest(_write(tmp_path, [bad])))
    assert any("member_path" in e for e in errors)


def test_generated_clip_needs_no_fetch(tmp_path: Path) -> None:
    gen = {
        "clip_id": "wn-1",
        "source": "eeper-generated",
        "fetch": {"mode": "", "url": ""},
        "sha256": "b" * 64,
        "license": "CC0-1.0",
        "attribution": "generated white noise",
        "labels": ["white_noise"],
        "role": "confuser",
        "category": "whitenoise_lullaby",
        "split": "eval",
        "verification_status": "verified",
        "generated": True,
        "gen": {"kind": "white_noise", "seed": 1, "duration": 4.0, "level": 0.2},
    }
    assert validate(load_manifest(_write(tmp_path, [gen]))) == []


def test_generated_clip_without_gen_spec_fails(tmp_path: Path) -> None:
    bad = {**_GOOD_CLIP, "clip_id": "g2", "generated": True, "fetch": {"mode": "", "url": ""}}
    errors = validate(load_manifest(_write(tmp_path, [bad])))
    assert any("gen.kind" in e for e in errors)
