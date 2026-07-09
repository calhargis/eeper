"""Deterministic scene synthesis (M2.0 criteria 2 + 6).

Fetches/generates every source clip in the manifest, arranges them into Scaper
foreground/background workspaces per split, and synthesizes nursery scenes: a cry
or confuser event placed over the nursery noise floor at a swept SNR, with light
room reverb. Everything is seeded per scene, so two builds from a clean cache
produce bit-identical WAV + annotation files (proven with Scaper 1.6.5 inside the
pinned container). Emits per-scene ``.wav`` + path-free ``.txt`` annotations and a
``scenes.json`` index that the checks consume.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zlib
from pathlib import Path
from typing import Any

import jams
import scaper

from eeper_fixtures import generate
from eeper_fixtures.fetch import Opener, fetch_clip
from eeper_fixtures.manifest import ClipSpec, Manifest

SR = 16000
SCENE_DURATION = 4.0
EVENT_DURATION = 1.5
REF_DB = -20
REVERB = 0.15

# scenes per (split, foreground label). Meets the eval floor (cry >= 100, confuser
# total >= 300, >= 30 per category) with margin; dev is a smaller disjoint split.
DEFAULT_COUNTS: dict[str, dict[str, int]] = {
    "eval": {"cry": 110, "speech": 80, "music_tv": 80, "pets": 80, "whitenoise_lullaby": 80},
    "dev": {"cry": 40, "speech": 40, "music_tv": 40, "pets": 40, "whitenoise_lullaby": 40},
}


def _label_for(clip: ClipSpec) -> str:
    if clip.role == "fg_cry":
        return "cry"
    if clip.role == "bg_floor":
        return "nursery"
    if clip.role == "confuser":
        return clip.category or "confuser"
    return clip.role


def _scene_seed(split: str, label: str, index: int) -> int:
    return zlib.crc32(f"{split}:{label}:{index}".encode()) & 0x7FFFFFFF


def prepare_sources(
    manifest: Manifest, cache_dir: Path, *, opener: Opener = urllib.request.urlopen
) -> dict[str, Path]:
    """Fetch or generate every source clip (content-addressed, verified). Sorted by
    clip_id for a stable creation order."""
    paths: dict[str, Path] = {}
    for clip in sorted(manifest.clips, key=lambda c: c.clip_id):
        if clip.generated:
            assert clip.gen is not None  # noqa: S101 (validated upstream)
            paths[clip.clip_id] = generate.ensure_generated(
                clip.clip_id, clip.gen, clip.sha256, cache_dir
            )
        else:
            paths[clip.clip_id] = fetch_clip(clip, cache_dir, opener=opener)
    return paths


def _build_workspace(
    manifest: Manifest, paths: dict[str, Path], work_dir: Path
) -> dict[str, tuple[Path, Path]]:
    """Lay out Scaper fg/bg dirs per split: fg/<label>/<clip>.wav, bg/nursery/<clip>.wav."""
    spaces: dict[str, tuple[Path, Path]] = {}
    for split in ("eval", "dev"):
        fg, bg = work_dir / split / "fg", work_dir / split / "bg"
        for clip in sorted(manifest.clips, key=lambda c: c.clip_id):
            if clip.split != split:
                continue
            label = _label_for(clip)
            dest_dir = (bg if clip.role == "bg_floor" else fg) / label
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{clip.clip_id}.wav"
            if not dest.exists():
                shutil.copy(paths[clip.clip_id], dest)
        spaces[split] = (fg, bg)
    return spaces


def _scene_from_jams(scene_id: str, split: str, label: str, jams_path: Path) -> dict[str, Any]:
    jam = jams.load(str(jams_path))
    ann = jam.search(namespace="scaper")[0]
    events: list[tuple[float, float, str]] = []
    sources: set[str] = set()
    fg_sources: set[str] = set()  # the foreground (cry/confuser) event's source, not the bg floor
    for obs in ann.data:
        events.append((float(obs.time), float(obs.time + obs.duration), obs.value["label"]))
        source_file = obs.value.get("source_file")
        if source_file:
            stem = Path(source_file).stem
            sources.add(stem)
            if obs.value.get("label") == label:
                fg_sources.add(stem)
    is_cry = label == "cry"
    return {
        "scene_id": scene_id,
        "split": split,
        "is_cry": is_cry,
        "category": None if is_cry else label,
        "duration": SCENE_DURATION,
        "events": sorted(events),
        "source_clip_ids": sorted(sources),
        "fg_source_clip_ids": sorted(fg_sources),
    }


def _synthesize(
    split: str, label: str, count: int, fg: Path, bg: Path, out_dir: Path
) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    split_out = out_dir / split
    split_out.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        sc = scaper.Scaper(
            SCENE_DURATION, str(fg), str(bg), random_state=_scene_seed(split, label, i)
        )
        sc.ref_db = REF_DB
        sc.sr = SR
        sc.add_background(
            label=("const", "nursery"), source_file=("choose", []), source_time=("const", 0)
        )
        sc.add_event(
            label=("const", label),
            source_file=("choose", []),
            source_time=("const", 0),
            event_time=("uniform", 0.0, SCENE_DURATION - EVENT_DURATION),
            event_duration=("const", EVENT_DURATION),
            snr=("uniform", 0, 15),
            pitch_shift=None,
            time_stretch=None,
        )
        scene_id = f"{split}_{label}_{i:04d}"
        wav = split_out / f"{scene_id}.wav"
        jams_path = wav.with_suffix(".jams")
        sc.generate(str(wav), str(jams_path), disable_sox_warnings=True, reverb=REVERB)
        scene = _scene_from_jams(scene_id, split, label, jams_path)
        # Path-free .txt annotation (the JAMS embeds absolute paths + versions, so it
        # is NOT part of the reproducibility hash; the .txt is).
        wav.with_suffix(".txt").write_text(
            "".join(f"{o:.6f}\t{f:.6f}\t{lab}\n" for o, f, lab in scene["events"])
        )
        jams_path.unlink()
        scenes.append(scene)
    return scenes


def build(
    manifest: Manifest,
    cache_dir: Path,
    out_dir: Path,
    *,
    counts: dict[str, dict[str, int]] | None = None,
    opener: Opener = urllib.request.urlopen,
) -> Path:
    """Produce the library under ``out_dir`` and return the scenes.json path."""
    counts = counts or DEFAULT_COUNTS
    paths = prepare_sources(manifest, cache_dir, opener=opener)
    spaces = _build_workspace(manifest, paths, cache_dir / "work")
    all_scenes: list[dict[str, Any]] = []
    for split in sorted(counts):
        fg, bg = spaces[split]
        for label, count in sorted(counts[split].items()):
            all_scenes.extend(_synthesize(split, label, count, fg, bg, out_dir))
    index = out_dir / "scenes.json"
    index.write_text(json.dumps({"scenes": all_scenes}, indent=2, sort_keys=True))
    return index


def library_hashes(out_dir: Path) -> dict[str, str]:
    """SHA-256 of every produced .wav + .txt, keyed by path relative to out_dir
    (for the two-build bit-identity comparison — criterion 2)."""
    hashes: dict[str, str] = {}
    for path in sorted(out_dir.rglob("*")):
        if path.suffix in (".wav", ".txt") and path.is_file():
            hashes[str(path.relative_to(out_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes
