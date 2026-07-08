"""YAMNet log-mel audio frontend — a fixed, versioned, pure-NumPy reimplementation
of the TensorFlow ``features.py`` used to train YAMNet.

The cry classifier is a split model: this deterministic frontend turns a 16 kHz
mono waveform into the 96x64 log-mel patches YAMNet's classifier body expects, and
the ONNX artifact does only the classification. Keeping the frontend as ordinary,
unit-testable NumPy (rather than opaque in-graph STFT ops) makes it reproducible
across amd64/arm64 and lets every future model reuse the same preprocessed input.

Verified against TensorFlow's ``tf.signal`` frontend to < 2e-5 per log-mel value
(and the full numpy-frontend -> ONNX path reproduces reference waveform-in YAMNet
to < 1e-5). The unit tests pin this against a committed reference fixture; bump
``VERSION`` if any constant here changes so a stale cached model/fixture is caught.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

VERSION = "yamnet-logmel-1"

# YAMNet frontend constants (from the canonical params.py; do not change without a
# VERSION bump — they must match the weights the ONNX classifier was trained with).
SAMPLE_RATE = 16000
_STFT_WINDOW = 400  # round(16000 * 0.025 s)
_STFT_HOP = 160  # round(16000 * 0.010 s)
_FFT = 512  # 2 ** ceil(log2(400))
_N_BINS = _FFT // 2 + 1  # 257
MEL_BANDS = 64
_MEL_MIN_HZ = 125.0
_MEL_MAX_HZ = 7500.0
_LOG_OFFSET = 0.001
PATCH_FRAMES = 96  # round((16000/160) * 0.96 s)
_PATCH_HOP = 48  # round((16000/160) * 0.48 s)

# Minimum samples for one patch, matching YAMNet's pad_waveform.
_MIN_SAMPLES = int((0.96 + 0.025 - 0.010) * SAMPLE_RATE)  # 15600
_PATCH_HOP_SAMPLES = int(0.48 * SAMPLE_RATE)  # 7680

_F32 = np.float32


def _hz_to_mel(hz: npt.ArrayLike) -> npt.NDArray[np.float64]:
    # HTK mel scale, matching tf.signal.linear_to_mel_weight_matrix.
    return 1127.0 * np.log(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def _mel_matrix() -> npt.NDArray[np.float32]:
    """The [257, 64] linear-spectrogram -> mel weight matrix (DC bin zeroed),
    matching ``tf.signal.linear_to_mel_weight_matrix``."""
    linear_hz = np.linspace(0.0, SAMPLE_RATE / 2.0, _N_BINS)[1:]  # drop DC bin
    bins_mel = _hz_to_mel(linear_hz)[:, np.newaxis]
    edges = np.linspace(_hz_to_mel(_MEL_MIN_HZ), _hz_to_mel(_MEL_MAX_HZ), MEL_BANDS + 2)
    lower, center, upper = edges[:-2], edges[1:-1], edges[2:]
    lower_slope = (bins_mel - lower) / (center - lower)
    upper_slope = (upper - bins_mel) / (upper - center)
    weights = np.maximum(0.0, np.minimum(lower_slope, upper_slope))
    return np.pad(weights, [[1, 0], [0, 0]]).astype(_F32)  # restore the zeroed DC row


# Periodic Hann window (tf.signal.stft default) + the mel matrix, computed once.
_HANN = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(_STFT_WINDOW) / _STFT_WINDOW)).astype(_F32)
_MEL = _mel_matrix()


def pcm_to_waveform(pcm: bytes) -> npt.NDArray[np.float32]:
    """s16le PCM bytes -> float32 waveform in [-1, 1) (YAMNet's expected input)."""
    return np.frombuffer(pcm, dtype="<i2").astype(_F32) / 32768.0


def pad_waveform(waveform: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """Zero-pad so the framing yields an integral number of patches (matches
    YAMNet's ``pad_waveform``)."""
    n = len(waveform)
    pad = max(0, _MIN_SAMPLES - n)
    after_first = max(n, _MIN_SAMPLES) - _MIN_SAMPLES
    hops = int(np.ceil(after_first / _PATCH_HOP_SAMPLES)) if _PATCH_HOP_SAMPLES else 0
    pad += _PATCH_HOP_SAMPLES * hops - after_first
    return np.pad(waveform, (0, pad)) if pad else waveform


def log_mel_patches(waveform: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """A 16 kHz mono float32 waveform -> ``[num_patches, 96, 64]`` log-mel patches.
    Returns an empty ``[0, 96, 64]`` array if there is not enough audio."""
    waveform = pad_waveform(np.asarray(waveform, dtype=_F32))
    n = len(waveform)
    n_frames = 1 + (n - _STFT_WINDOW) // _STFT_HOP if n >= _STFT_WINDOW else 0
    if n_frames <= 0:
        return np.empty((0, PATCH_FRAMES, MEL_BANDS), dtype=_F32)
    frames = np.stack(
        [waveform[i * _STFT_HOP : i * _STFT_HOP + _STFT_WINDOW] for i in range(n_frames)]
    )
    magnitude = np.abs(np.fft.rfft(frames * _HANN, n=_FFT, axis=1))  # [n_frames, 257]
    log_mel = np.log(magnitude.astype(_F32) @ _MEL + _LOG_OFFSET)  # [n_frames, 64]
    n_patches = 1 + (n_frames - PATCH_FRAMES) // _PATCH_HOP if n_frames >= PATCH_FRAMES else 0
    if n_patches <= 0:
        return np.empty((0, PATCH_FRAMES, MEL_BANDS), dtype=_F32)
    patches = np.stack(
        [log_mel[i * _PATCH_HOP : i * _PATCH_HOP + PATCH_FRAMES] for i in range(n_patches)]
    )
    return patches.astype(_F32)
