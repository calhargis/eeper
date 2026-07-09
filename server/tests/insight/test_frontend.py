"""The log-mel frontend is fixed + versioned: it must reproduce a committed
reference fixture. The fixture files carry a SHA-256 (tamper/drift detection,
arch-independent); the recompute is checked with a tolerance because float32
FFT/matmul differs by a few ULP across BLAS builds / CPU arches."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from eeper.insight import frontend

_FIXTURES = Path(__file__).parent / "fixtures" / "frontend"
# SHA-256 of the committed reference files — recorded at generation time.
_WAVEFORM_SHA = "9a17b6b67c122f07e22be2b6dcb4c171fe2d9f281177d1338a3d0250c0acbf72"
_PATCHES_SHA = "187f0744dc929bc94cf597a2ed5d29eaecc65cbbb18db4d4d72be5cae08a58ce"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_fixtures_are_intact() -> None:
    # Guards the committed fixture bytes (mirrors the model-manifest tamper check).
    assert _sha(_FIXTURES / "waveform.npy") == _WAVEFORM_SHA
    assert _sha(_FIXTURES / "patches.npy") == _PATCHES_SHA


def test_frontend_reproduces_reference_patches() -> None:
    waveform = np.load(_FIXTURES / "waveform.npy")
    expected = np.load(_FIXTURES / "patches.npy")
    got = frontend.log_mel_patches(waveform)
    assert got.shape == expected.shape == (2, frontend.PATCH_FRAMES, frontend.MEL_BANDS)
    assert got.dtype == np.float32
    # A real frontend change (wrong window/mel/log) shifts values by >> 1e-4; ULP-level
    # cross-arch noise is < 1e-5.
    assert np.allclose(got, expected, atol=1e-4), f"max diff {np.max(np.abs(got - expected)):.2e}"


def test_pcm_to_waveform_scales_int16() -> None:
    pcm = np.array([0, 32767, -32768, 16384], dtype="<i2").tobytes()
    wav = frontend.pcm_to_waveform(pcm)
    assert wav.dtype == np.float32
    assert wav[0] == 0.0
    assert abs(wav[1] - 0.99997) < 1e-4  # 32767/32768
    assert wav[2] == -1.0
    assert abs(wav[3] - 0.5) < 1e-6


def test_short_audio_pads_to_one_patch() -> None:
    # Less than one patch window of audio still yields exactly one patch (padded).
    patches = frontend.log_mel_patches(np.zeros(8000, dtype=np.float32))
    assert patches.shape == (1, frontend.PATCH_FRAMES, frontend.MEL_BANDS)


def test_longer_audio_yields_more_patches() -> None:
    # ~2 s of audio hops into multiple overlapping patches.
    patches = frontend.log_mel_patches(np.zeros(32000, dtype=np.float32))
    assert patches.shape[0] >= 3
    assert patches.shape[1:] == (frontend.PATCH_FRAMES, frontend.MEL_BANDS)
