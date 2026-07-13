#!/usr/bin/env python3
"""Clinical-terms copy lint (M4.2).

Fails if any user-facing copy uses medical / diagnostic / vital-sign / alarm framing.
eeper is a sleep-insight tool, not a medical device — this enforces the CONTRIBUTING.md
safety boundary in CI instead of leaving it to review. Neutral feature words (heart rate,
SpO2, oxygen) are allowed as trend context; what's banned is medical/diagnostic CLAIMS and
ALARM framing. The reviewed pulse-ox disclaimer (``eeper/api/pulseox_copy.py``) is the
single exemption — it alone may name the concepts it disclaims. Put ``clinical-terms-ok``
on a line to mark a reviewed exception.

Pure stdlib. Run from anywhere:  python server/scripts/clinical_terms_lint.py
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# "not a medical device" is fine; "medical-grade" / "vital sign" are not. Bare apnea/SIDS
# should never appear in user copy (only the exempt disclaimer names them).
_DENYLIST = (
    r"\bapnea\b",
    r"\bsids\b",
    r"\bvital[\s-]?signs?\b",
    r"\bmedical[\s-]?grade\b",
    r"\blife[\s-]?threatening\b",
    r"\bdesaturations?\b",
    r"\b(?:oxygen|blood[\s-]?oxygen|spo2)[\s-]+(?:alarm|alert|warning)s?\b",
    r"\bheart[\s-]?rate[\s-]+(?:alarm|alert)s?\b",
    r"\bdetects?\b[\w\s]{0,15}\b(?:apnea|breathing|sids|oxygen)\b",
    r"\bprevents?\b[\w\s]{0,15}\b(?:sids|apnea|death)\b",
    r"\bbreathing[\s-]+(?:monitor|detection|alarm)\b",
    r"\bkeeps?[\s-]+(?:your[\s-]|the[\s-])?(?:baby|child|infant)[\s-]+safe\b",
    r"\b(?:alarms?|alerts?)\b[\w\s]{0,20}\b(?:oxygen|heart[\s-]?rate|breathing|spo2)\b",
)
_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in _DENYLIST)
_MARKER = "clinical-terms-ok"

_EXTS = {".svelte", ".ts", ".js", ".py"}
# The reviewed safety copy — the ONLY place allowed to name what it disclaims.
_EXEMPT = frozenset({_ROOT / "server" / "eeper" / "api" / "pulseox_copy.py"})


def repo_targets() -> list[Path]:
    """The user-facing copy the lint guards: the web UI + the push-notification templates."""
    return [
        _ROOT / "web" / "src",
        _ROOT / "server" / "eeper" / "api" / "push_service.py",
    ]


def _iter_files(targets: Iterable[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file():
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                if path.is_file() and path.suffix in _EXTS:
                    yield path


def _strip_comment(line: str, suffix: str) -> str:
    """Drop the code-comment part of a line — developer comments (which legitimately
    document the safety stance, e.g. "never a vital-sign readout") are not user copy. Only
    user-facing text is scanned. ``//`` after ``:`` (a URL) is preserved."""
    if suffix == ".py":
        return re.sub(r"#.*$", "", line)
    line = re.sub(r"<!--.*?-->", "", line)
    return re.sub(r"(?<!:)//.*$", "", line)


def find_violations(targets: Iterable[Path]) -> list[str]:
    """Return ``file:line: match`` for every denylisted phrase in the targets' user copy."""
    out: list[str] = []
    for path in _iter_files(targets):
        if path in _EXEMPT:
            continue
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _MARKER in raw:
                continue
            line = _strip_comment(raw, path.suffix)
            for pattern in _PATTERNS:
                match = pattern.search(line)
                if match is not None:
                    rel = path.relative_to(_ROOT) if path.is_relative_to(_ROOT) else path
                    out.append(f"{rel}:{lineno}: clinical/alarm framing: {match.group(0)!r}")
    return out


def main() -> int:
    violations = find_violations(repo_targets())
    if violations:
        print("Clinical-terms lint FAILED — user-facing copy must not make medical/alarm claims:")
        for v in violations:
            print(f"  {v}")
        print(
            "\nUse neutral, insights-only wording. The pulse-ox disclaimer is the only exemption."
        )
        return 1
    print("Clinical-terms lint passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
