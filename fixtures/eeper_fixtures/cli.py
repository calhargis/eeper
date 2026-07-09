"""``fixtures`` CLI: verify | build | check | repro.

CI runs: verify (manifest integrity + source-split disjointness) -> build (twice,
clean cache) -> repro (bit-identity) -> check (floor + annotations + scene-split
leaks). Each command exits non-zero on failure with the errors on stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eeper_fixtures.checks import (
    check_annotations,
    check_floor,
    check_scene_splits,
    check_splits,
    load_scenes,
)
from eeper_fixtures.manifest import load_manifest, validate
from eeper_fixtures.provenance import render as render_provenance


def _report(errors: list[str]) -> int:
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} error(s)", file=sys.stderr)
        return 1
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    return _report(validate(manifest) + check_splits(manifest))


def _cmd_build(args: argparse.Namespace) -> int:
    from eeper_fixtures import build as build_mod  # lazy: pulls scaper/jams

    manifest = load_manifest(args.manifest)
    errors = validate(manifest)
    if errors:
        return _report(errors)
    counts = build_mod.DEFAULT_COUNTS
    if args.max_per_label:
        counts = {
            split: {label: min(n, args.max_per_label) for label, n in per.items()}
            for split, per in counts.items()
        }
    index = build_mod.build(manifest, args.cache, args.out, counts=counts)
    scenes = load_scenes(index)
    print(f"built {len(scenes)} scenes -> {args.out}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    scenes = load_scenes(args.out / "scenes.json")
    errors = check_splits(manifest) + check_scene_splits(scenes) + check_annotations(scenes)
    if not args.skip_floor:
        errors += check_floor(scenes)
    return _report(errors)


def _cmd_provenance(args: argparse.Namespace) -> int:
    text = render_provenance(load_manifest(args.manifest))
    if args.out:
        args.out.write_text(text)
    else:
        print(text)
    return 0


def _cmd_repro(args: argparse.Namespace) -> int:
    from eeper_fixtures import build as build_mod  # lazy: keeps the CLI importable without deps

    hashes_a = build_mod.library_hashes(args.a)
    hashes_b = build_mod.library_hashes(args.b)
    if not hashes_a:
        return _report([f"no library artifacts under {args.a}"])
    errors: list[str] = []
    for path in sorted(set(hashes_a) | set(hashes_b)):
        if hashes_a.get(path) != hashes_b.get(path):
            errors.append(f"non-reproducible: {path} differs between builds")
    if errors:
        return _report(errors)
    print(f"reproducible: {len(hashes_a)} artifacts bit-identical across builds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixtures", description="eeper audio fixture library")
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify", help="validate the manifest + source-split disjointness")

    p_build = sub.add_parser("build", help="fetch/generate sources and synthesize scenes")
    p_build.add_argument("--out", type=Path, required=True)
    p_build.add_argument("--cache", type=Path, default=Path("cache"))
    p_build.add_argument("--max-per-label", type=int, default=0, help="cap scenes/label (testing)")

    p_check = sub.add_parser(
        "check", help="run floor + annotation + split gates on a built library"
    )
    p_check.add_argument("--out", type=Path, required=True)
    p_check.add_argument("--skip-floor", action="store_true", help="skip the statistical floor")

    p_repro = sub.add_parser("repro", help="assert two built libraries are bit-identical")
    p_repro.add_argument("--a", type=Path, required=True)
    p_repro.add_argument("--b", type=Path, required=True)

    p_prov = sub.add_parser("provenance", help="render the license/provenance report")
    p_prov.add_argument("--out", type=Path, default=None)

    args = parser.parse_args(argv)
    return {
        "verify": _cmd_verify,
        "build": _cmd_build,
        "check": _cmd_check,
        "repro": _cmd_repro,
        "provenance": _cmd_provenance,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
