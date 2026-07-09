"""Generate the license/provenance report from the manifest (spec §M2.0)."""

from __future__ import annotations

from collections import defaultdict

from eeper_fixtures.manifest import Manifest

_ODBL_NOTE = (
    "> **ODbL share-alike note:** the donateacry-corpus cry clips are licensed ODbL-1.0. "
    "This repository stores only references + checksums (no audio), so no ODbL database is "
    "redistributed. If a derived subset of these clips is ever redistributed, it must be made "
    "available under ODbL-1.0 with attribution."
)


def render(manifest: Manifest) -> str:
    by_source: dict[str, set[str]] = defaultdict(set)
    licenses: dict[str, int] = defaultdict(int)
    attributions: dict[str, set[str]] = defaultdict(set)
    for clip in manifest.clips:
        by_source[clip.source].add(clip.license)
        licenses[clip.license] += 1
        attributions[clip.source].add(clip.attribution)

    lines = [
        f"# Provenance — {manifest.fixture_version}",
        "",
        "Generated from `manifest.json` by `fixtures provenance`. No third-party audio is",
        "committed; each source clip is fetched at build time and checksum-verified.",
        "",
        "## Licenses in use",
        "",
        "| License | clips |",
        "| --- | --- |",
    ]
    lines += [f"| {lic} | {n} |" for lic, n in sorted(licenses.items())]
    lines += ["", "## Sources", ""]
    for source in sorted(by_source):
        lines.append(f"### {source}")
        lines.append(f"- licenses: {', '.join(sorted(by_source[source]))}")
        for attribution in sorted(attributions[source]):
            lines.append(f"- {attribution}")
        lines.append("")
    lines += [_ODBL_NOTE, ""]
    return "\n".join(lines)
