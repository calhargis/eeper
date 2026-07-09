"""C1 (M2.3): the fetch tool downloads models, verifies SHA-256, and refuses a
tampered file. No network — an injected opener serves bytes in-process."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from eeper.insight.modelfetch import ModelFetchError, ensure_models, fetch_model, load_manifest

_GOOD = b"pretend-onnx-model-bytes" * 100
_GOOD_SHA = hashlib.sha256(_GOOD).hexdigest()


def _manifest(tmp: Path, sha: str) -> Path:
    path = tmp / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "name": "yamnet",
                        "version": "1",
                        "filename": "yamnet.onnx",
                        "url": "https://example.test/yamnet.onnx",
                        "sha256": sha,
                    }
                ],
            }
        )
    )
    return path


def _opener(payload: bytes) -> Callable[[str], io.BytesIO]:
    def open_url(_url: str) -> io.BytesIO:
        return io.BytesIO(payload)

    return open_url


def test_downloads_and_verifies(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, _GOOD_SHA)
    cache = tmp_path / "cache"
    (spec,) = load_manifest(manifest)
    path = fetch_model(spec, cache, opener=_opener(_GOOD))
    assert path.read_bytes() == _GOOD
    assert path.name == "yamnet.onnx"


def test_refuses_tampered_file(tmp_path: Path) -> None:
    # Manifest expects the good checksum, but the server returns tampered bytes.
    manifest = _manifest(tmp_path, _GOOD_SHA)
    cache = tmp_path / "cache"
    (spec,) = load_manifest(manifest)
    with pytest.raises(ModelFetchError, match="checksum mismatch"):
        fetch_model(spec, cache, opener=_opener(_GOOD + b"tampered"))
    # No file is left behind for a downstream loader to pick up.
    assert not (cache / "yamnet.onnx").exists()
    assert list(cache.glob("*.part")) == []


def test_reuses_valid_cache_without_refetch(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, _GOOD_SHA)
    cache = tmp_path / "cache"
    (spec,) = load_manifest(manifest)
    fetch_model(spec, cache, opener=_opener(_GOOD))

    def _fail(_url: str) -> io.BytesIO:
        raise AssertionError("should not re-download a valid cached model")

    path = fetch_model(spec, cache, opener=_fail)
    assert path.read_bytes() == _GOOD


def test_refetches_when_cache_is_corrupt(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, _GOOD_SHA)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "yamnet.onnx").write_bytes(b"corrupt cached bytes")  # wrong checksum
    (spec,) = load_manifest(manifest)
    path = fetch_model(spec, cache, opener=_opener(_GOOD))
    assert path.read_bytes() == _GOOD


def test_ensure_models_returns_named_paths(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, _GOOD_SHA)
    paths = ensure_models(manifest, tmp_path / "cache", opener=_opener(_GOOD))
    assert set(paths) == {"yamnet"}
    assert paths["yamnet"].read_bytes() == _GOOD
