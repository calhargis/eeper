#!/usr/bin/env python3
"""Assemble ``fixtures/manifest.json`` from pinned sources (occasional maintainer
tool; no third-party audio is committed — only URLs + checksums).

Deterministic selection (sorted, fixed picks) so re-running reproduces the same
manifest. Cry positives come from donateacry-corpus (ODbL) at a pinned commit;
confuser speech/music/pets come from FSD50K via the sumin0223 clip mirror (bytes,
pinned commit) with per-clip license from the Fhrozen metadata — CC0/CC-BY only,
by-nc + sampling+ dropped. white-noise/lullaby + the nursery floor are generated.

Run inside the fixtures build container (network on):
    python fixtures/tools/build_manifest.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from eeper_fixtures import generate  # noqa: E402
from eeper_fixtures.manifest import Gen  # noqa: E402

DONATEACRY_PIN = "6baf5aca0460964ad072653585837f968a87c2af"
FSD_CLIPS_PIN = "a8a1536a2704b517b5cc623af64d5b0e1506faa9"
DONATEACRY_API = (
    "https://api.github.com/repos/gveres/donateacry-corpus/contents/"
    f"donateacry_corpus_cleaned_and_updated_data/{{cat}}?ref={DONATEACRY_PIN}"
)
FSD_META = "https://huggingface.co/datasets/Fhrozen/FSD50k/resolve/main"
FSD_CLIPS = f"https://huggingface.co/datasets/sumin0223/FSD50k/resolve/{FSD_CLIPS_PIN}/clips"

CRY_CATEGORIES = ["belly_pain", "burping", "discomfort", "hungry", "tired"]
CRY_EVAL_PER_CAT, CRY_DEV_PER_CAT = 8, 4
FSD_TARGETS = {
    "speech": {"Speech", "Male_speech_and_man_speaking", "Female_speech_and_woman_speaking"},
    "music_tv": {"Music"},
    "pets": {"Dog", "Bark", "Cat", "Meow", "Domestic_animals_and_pets", "Growling", "Purr"},
}
FSD_EVAL_PER_CAT, FSD_DEV_PER_CAT = 32, 20

_cache = _ROOT / "cache" / "meta"


def _get(url: str) -> bytes:
    return urllib.request.urlopen(url).read()  # noqa: S310 (pinned https)


def _cached(url: str, name: str) -> bytes:
    _cache.mkdir(parents=True, exist_ok=True)
    path = _cache / name
    if not path.exists():
        path.write_bytes(_get(url))
    return path.read_bytes()


def _spdx(license_url: str) -> str | None:
    if "publicdomain/zero" in license_url:
        return "CC0-1.0"
    if "licenses/by/3.0" in license_url:
        return "CC-BY-3.0"
    if "licenses/by/4.0" in license_url:
        return "CC-BY-4.0"
    return None  # by-nc, sampling+, etc. -> excluded


def _cry_clips() -> list[dict]:
    clips: list[dict] = []
    for category in CRY_CATEGORIES:
        listing = json.loads(_get(DONATEACRY_API.format(cat=category)))
        wavs = sorted((e for e in listing if e["name"].endswith(".wav")), key=lambda e: e["name"])
        chosen = wavs[: CRY_EVAL_PER_CAT + CRY_DEV_PER_CAT]
        for i, entry in enumerate(chosen):
            split = "eval" if i < CRY_EVAL_PER_CAT else "dev"
            data = _get(entry["download_url"])
            clips.append(
                {
                    "clip_id": f"cry-{category}-{i:02d}",
                    "source": "donateacry",
                    "fetch": {"mode": "direct", "url": entry["download_url"]},
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "license": "ODbL-1.0",
                    "attribution": "donateacry-corpus (github.com/gveres/donateacry-corpus)",
                    "labels": ["cry", category],
                    "role": "fg_cry",
                    "split": split,
                    "verification_status": "pending",
                }
            )
        print(f"  cry/{category}: {len(chosen)} clips", flush=True)
    return clips


def _fsd_clips() -> list[dict]:
    clips: list[dict] = []
    for fsd_split, per_cat in (("eval", FSD_EVAL_PER_CAT), ("dev", FSD_DEV_PER_CAT)):
        info = json.loads(
            _cached(
                f"{FSD_META}/metadata/{fsd_split}_clips_info_FSD50K.json", f"{fsd_split}_info.json"
            )
        )
        rows = list(
            csv.DictReader(
                (_cached(f"{FSD_META}/labels/{fsd_split}.csv", f"{fsd_split}.csv"))
                .decode()
                .splitlines()
            )
        )
        my_split = "eval" if fsd_split == "eval" else "dev"
        for category, names in FSD_TARGETS.items():
            candidates = sorted(
                row["fname"]
                for row in rows
                if set(row["labels"].split(",")) & names
                and _spdx(info.get(row["fname"], {}).get("license", "")) is not None
            )
            for fid in candidates[:per_cat]:
                url = f"{FSD_CLIPS}/{fsd_split}/{fid}.wav"
                data = _get(url)
                clips.append(
                    {
                        "clip_id": f"fsd-{category}-{fsd_split}-{fid}",
                        "source": "fsd50k",
                        "fetch": {"mode": "direct", "url": url},
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "license": _spdx(info[fid]["license"]),
                        "attribution": f"FSD50K / Freesound id {fid}",
                        "labels": [
                            category,
                            *sorted(
                                set(next(r["labels"] for r in rows if r["fname"] == fid).split(","))
                            ),
                        ],
                        "role": "confuser",
                        "category": category,
                        "split": my_split,
                        "verification_status": "pending",
                    }
                )
            print(
                f"  fsd/{category}/{fsd_split}: {min(per_cat, len(candidates))} clips", flush=True
            )
    return clips


def _generated_clip(
    clip_id: str, gen: Gen, category: str | None, role: str, split: str, label: str
) -> dict:
    entry = {
        "clip_id": clip_id,
        "source": "eeper-generated",
        "fetch": {"mode": "", "url": ""},
        "sha256": generate.generated_sha256(gen),
        "license": "CC0-1.0",
        "attribution": "eeper generated (CC0)",
        "labels": [label],
        "role": role,
        "split": split,
        "verification_status": "verified",
        "generated": True,
        "gen": {"kind": gen.kind, "seed": gen.seed, "duration": gen.duration, "level": gen.level},
    }
    if category:
        entry["category"] = category
    return entry


def _generated_clips() -> list[dict]:
    clips: list[dict] = []
    for split, wl_count, seed_base in (("eval", 6, 0), ("dev", 4, 500)):
        for i in range(wl_count):
            clips.append(
                _generated_clip(
                    f"wn-{split}-{i:02d}",
                    Gen("white_noise", seed_base + i, 3.0, 0.2),
                    "whitenoise_lullaby",
                    "confuser",
                    split,
                    "white_noise",
                )
            )
            clips.append(
                _generated_clip(
                    f"lull-{split}-{i:02d}",
                    Gen("lullaby", seed_base + 100 + i, 3.0, 0.2),
                    "whitenoise_lullaby",
                    "confuser",
                    split,
                    "lullaby",
                )
            )
        for i in range(2):
            clips.append(
                _generated_clip(
                    f"nursery-{split}-{i:02d}",
                    Gen("nursery_floor", seed_base + 900 + i, 5.0, 0.03),
                    None,
                    "bg_floor",
                    split,
                    "nursery",
                )
            )
    return clips


def main() -> int:
    print("cry (donateacry)…", flush=True)
    clips = _cry_clips()
    print("confusers (fsd50k)…", flush=True)
    clips += _fsd_clips()
    print("generated…", flush=True)
    clips += _generated_clips()
    manifest = {"schema_version": 1, "fixture_version": "fixtures-v1", "clips": clips}
    out = _ROOT / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(clips)} clips -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
