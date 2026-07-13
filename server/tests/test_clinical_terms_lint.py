"""Tests for the clinical-terms copy lint (M4.2): it catches medical/alarm framing in
user-facing copy, ignores developer comments + reviewed exemptions, and the repo's
current copy is clean.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from clinical_terms_lint import find_violations, repo_targets  # noqa: E402


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_repo_copy_is_clean() -> None:
    # The whole repo's user-facing copy must pass — this is the gate CI runs.
    assert find_violations(repo_targets()) == []


@pytest.mark.parametrize(
    "bad",
    [
        "<p>alerts you when oxygen drops</p>",
        "<p>detects apnea while sleeping</p>",
        "<h1>medical-grade monitoring</h1>",
        "<span>shows vital signs</span>",
        "<p>keeps your baby safe all night</p>",
        "<p>SpO2 alarm</p>",
    ],
)
def test_catches_medical_and_alarm_framing(tmp_path: Path, bad: str) -> None:
    assert find_violations([_write(tmp_path, "copy.svelte", bad)])


def test_ignores_developer_comments_and_urls(tmp_path: Path) -> None:
    # A code comment documenting the safety stance is not user copy; a URL isn't a claim.
    ok = (
        "// eeper never shows vital signs or oxygen alarms\n"
        '<a href="https://safetosleep.gov/">safe sleep</a>'
    )
    assert find_violations([_write(tmp_path, "fine.svelte", ok)]) == []


def test_reviewed_marker_exempts_a_line(tmp_path: Path) -> None:
    text = "<p>detects apnea</p> <!-- clinical-terms-ok: reviewed safety copy -->"
    assert find_violations([_write(tmp_path, "exempt.svelte", text)]) == []


def test_neutral_feature_wording_is_allowed(tmp_path: Path) -> None:
    # Heart rate / SpO2 as trend context (no alarm/claim) must NOT be flagged.
    ok = "<p>Heart-rate variability as a sleep-state feature — trend context only.</p>"
    assert find_violations([_write(tmp_path, "neutral.svelte", ok)]) == []
