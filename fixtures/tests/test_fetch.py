"""Criterion 3: fetch verification — SHA-256 checked, tampered sources refused.
No network: an in-process opener serves bytes (direct) or an in-memory zip (member)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from eeper_fixtures.fetch import FixtureFetchError, fetch_clip
from eeper_fixtures.manifest import ClipSpec, Fetch

_CLIP_BYTES = b"RIFF....fake-wav-bytes...." * 50
_CLIP_SHA = hashlib.sha256(_CLIP_BYTES).hexdigest()


def _opener(payload: bytes):
    def open_url(_url: str) -> io.BytesIO:
        return io.BytesIO(payload)

    return open_url


def _direct(sha: str) -> ClipSpec:
    return ClipSpec(
        clip_id="c1",
        source="fsd50k",
        fetch=Fetch(mode="direct", url="https://example.test/c1.wav"),
        sha256=sha,
        license="CC0-1.0",
        attribution="x",
        labels=("speech",),
        role="confuser",
        split="eval",
        verification_status="verified",
        category="speech",
    )


def test_direct_fetch_verifies(tmp_path: Path) -> None:
    path = fetch_clip(_direct(_CLIP_SHA), tmp_path, opener=_opener(_CLIP_BYTES))
    assert path.read_bytes() == _CLIP_BYTES
    assert path.name == "c1.wav"


def test_direct_tamper_refused(tmp_path: Path) -> None:
    with pytest.raises(FixtureFetchError, match="checksum mismatch"):
        fetch_clip(_direct(_CLIP_SHA), tmp_path, opener=_opener(_CLIP_BYTES + b"x"))
    assert (
        list((tmp_path / "clips").glob("*")) == [] or not (tmp_path / "clips" / "c1.wav").exists()
    )


def test_valid_cache_not_refetched(tmp_path: Path) -> None:
    fetch_clip(_direct(_CLIP_SHA), tmp_path, opener=_opener(_CLIP_BYTES))

    def _fail(_url: str) -> io.BytesIO:
        raise AssertionError("should not re-download a valid cached clip")

    assert fetch_clip(_direct(_CLIP_SHA), tmp_path, opener=_fail).read_bytes() == _CLIP_BYTES


def _zip_with(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, payload)
    return buf.getvalue()


def _archive_clip(member_sha: str, archive_sha: str) -> ClipSpec:
    return ClipSpec(
        clip_id="rir1",
        source="rir-slr26",
        fetch=Fetch(
            mode="archive-member",
            url="https://example.test/rirs.zip",
            member_path="rirs/room1.wav",
            archive_sha256=archive_sha,
        ),
        sha256=member_sha,
        license="Apache-2.0",
        attribution="OpenSLR SLR26",
        labels=("rir",),
        role="rir",
        split="eval",
        verification_status="verified",
    )


def test_archive_member_extract_and_verify(tmp_path: Path) -> None:
    member_bytes = b"impulse-response-bytes" * 20
    archive = _zip_with("rirs/room1.wav", member_bytes)
    clip = _archive_clip(
        hashlib.sha256(member_bytes).hexdigest(), hashlib.sha256(archive).hexdigest()
    )
    path = fetch_clip(clip, tmp_path, opener=_opener(archive))
    assert path.read_bytes() == member_bytes


def test_archive_tamper_refused(tmp_path: Path) -> None:
    member_bytes = b"impulse" * 20
    archive = _zip_with("rirs/room1.wav", member_bytes)
    # Manifest expects the good member sha, but the archive's own checksum is wrong.
    clip = _archive_clip(hashlib.sha256(member_bytes).hexdigest(), "f" * 64)
    with pytest.raises(FixtureFetchError, match="checksum mismatch"):
        fetch_clip(clip, tmp_path, opener=_opener(archive))
