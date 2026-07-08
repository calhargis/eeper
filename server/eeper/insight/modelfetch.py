"""Model-fetch tooling (M2.3): download the pretrained models named in the
manifest, verify each against its SHA-256, and refuse a tampered/corrupt file.

Models are versioned artifacts fetched at first run (never committed / baked into
images). The manifest (name, version, filename, url, sha256) is the source of
truth; a checksum mismatch — whether from tampering, a truncated download, or a
stale cache — raises rather than loading an unverified model.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

_log = logging.getLogger("eeper.insight.modelfetch")
_CHUNK = 1 << 20

Opener = Callable[[str], IO[bytes]]


class ModelFetchError(RuntimeError):
    """A model could not be fetched or failed verification."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    version: str
    filename: str
    url: str
    sha256: str


def load_manifest(path: Path) -> list[ModelSpec]:
    data = json.loads(Path(path).read_text())
    return [
        ModelSpec(
            name=m["name"],
            version=m["version"],
            filename=m["filename"],
            url=m["url"],
            sha256=m["sha256"].lower(),
        )
        for m in data.get("models", [])
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_model(
    spec: ModelSpec, cache_dir: Path, *, opener: Opener = urllib.request.urlopen
) -> Path:
    """Return the cached path for ``spec``, downloading + verifying if needed.

    A cached file that already matches the checksum is reused. Otherwise the model
    is downloaded to a temp file, checksum-verified, and atomically renamed into
    place. A mismatch raises :class:`ModelFetchError` and leaves no file behind, so
    an unverified/tampered artifact is never loaded."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / spec.filename
    if target.exists():
        if _sha256(target) == spec.sha256:
            return target
        _log.warning("cached %s failed checksum; re-fetching", spec.filename)
        target.unlink()

    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, opener(spec.url) as resp:
            shutil.copyfileobj(resp, out)
        actual = _sha256(tmp)
        if actual != spec.sha256:
            raise ModelFetchError(
                f"{spec.name}: checksum mismatch (expected {spec.sha256}, got {actual}) "
                "— refusing to load a tampered or corrupt model"
            )
        os.replace(tmp, target)  # atomic; a reader never sees a partial/unverified file
        _log.info("fetched model %s v%s -> %s", spec.name, spec.version, target)
        return target
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def ensure_models(
    manifest_path: Path, cache_dir: Path, *, opener: Opener = urllib.request.urlopen
) -> dict[str, Path]:
    """Fetch + verify every model in the manifest; return {name: cached path}."""
    return {
        spec.name: fetch_model(spec, cache_dir, opener=opener)
        for spec in load_manifest(manifest_path)
    }
