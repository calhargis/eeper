"""Content-addressed source-clip fetch (M2.0 criterion 3).

Every clip is fetched by an immutable reference and verified against the manifest's
SHA-256 before it enters the build; a mismatch (tampered/substituted/corrupt source)
raises and leaves no file behind. Two modes: ``direct`` (a stable per-clip URL) and
``archive-member`` (download a pinned archive, verify its own checksum, then extract
and hash ONE member). Modeled on the verified server ``modelfetch.py``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import IO

from eeper_fixtures.manifest import ClipSpec, Fetch

Opener = Callable[[str], IO[bytes]]
_CHUNK = 1 << 20


class FixtureFetchError(RuntimeError):
    """A source clip could not be fetched or failed verification."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip_suffix(clip: ClipSpec) -> str:
    ref = clip.fetch.member_path or clip.fetch.url
    return Path(ref).suffix or ".wav"


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _download(url: str, opener: Opener) -> bytes:
    with opener(url) as resp:
        return resp.read()


def _fetch_archive(fetch: Fetch, cache_dir: Path, opener: Opener) -> Path:
    """Download + verify a pinned archive, cached by its own sha256 for reuse."""
    if not fetch.archive_sha256:
        raise FixtureFetchError(f"archive-member fetch of {fetch.url} needs archive_sha256")
    archives = cache_dir / "archives"
    suffix = "".join(Path(fetch.url).suffixes)
    target = archives / f"{fetch.archive_sha256}{suffix}"
    if target.exists() and _sha256_file(target) == fetch.archive_sha256:
        return target
    data = _download(fetch.url, opener)
    actual = _sha256_bytes(data)
    if actual != fetch.archive_sha256:
        raise FixtureFetchError(
            f"archive {fetch.url}: checksum mismatch (expected {fetch.archive_sha256}, "
            f"got {actual}) — refusing tampered archive"
        )
    _atomic_write(target, data)
    return target


def _extract_member(archive: Path, member_path: str) -> bytes:
    """Read ONE named member's bytes (never extractall — no path-traversal writes)."""
    if archive.name.endswith((".zip",)):
        with zipfile.ZipFile(archive) as zf:
            return zf.read(member_path)
    if ".tar" in archive.suffixes or archive.name.endswith((".tgz",)):
        with tarfile.open(archive) as tf:
            handle = tf.extractfile(member_path)
            if handle is None:
                raise FixtureFetchError(f"member {member_path!r} not found in {archive.name}")
            return handle.read()
    raise FixtureFetchError(f"unsupported archive type: {archive.name}")


def fetch_clip(clip: ClipSpec, cache_dir: Path, *, opener: Opener = urllib.request.urlopen) -> Path:
    """Return the cached path for ``clip``, fetching + verifying if needed. Raises
    :class:`FixtureFetchError` on a checksum mismatch (leaving no file behind)."""
    if clip.generated:
        raise FixtureFetchError(f"{clip.clip_id}: generated clip is synthesized, not fetched")
    cache_dir = Path(cache_dir)
    target = cache_dir / "clips" / f"{clip.clip_id}{_clip_suffix(clip)}"
    if target.exists() and _sha256_file(target) == clip.sha256:
        return target

    if clip.fetch.mode == "direct":
        data = _download(clip.fetch.url, opener)
    elif clip.fetch.mode == "archive-member":
        assert clip.fetch.member_path is not None  # noqa: S101 (validated upstream)
        archive = _fetch_archive(clip.fetch, cache_dir, opener)
        data = _extract_member(archive, clip.fetch.member_path)
    else:
        raise FixtureFetchError(f"{clip.clip_id}: unknown fetch mode {clip.fetch.mode!r}")

    actual = _sha256_bytes(data)
    if actual != clip.sha256:
        raise FixtureFetchError(
            f"{clip.clip_id}: checksum mismatch (expected {clip.sha256}, got {actual}) "
            "— refusing tampered or corrupt clip"
        )
    _atomic_write(target, data)
    return target
