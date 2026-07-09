"""Long-form nursery scene synthesis: sustained cry episodes and confuser-only
"nights", composed deterministically from clip waveforms (M2.3 episode gate; reused
by M3.3's full-night traces).

Short single-clip scenes answer "does the classifier fire on this sound?"; they can't
answer the questions a parent actually has — "will I be told when my baby is genuinely
crying?" and "will it cry wolf while I sleep?". Those are EPISODE and NIGHT questions,
so the gate needs episode- and night-length audio:

* a **cry episode** is a sustained spell — cry bursts (with within-infant variation)
  over a chosen interval on a quiet noise floor, at a known onset.
* a **confuser night** is hours of confuser events (TV, pets, white-noise machine,
  speech) at a realistic occupancy on the floor, with no cry at all.

Everything is seeded and deterministic (given the input waveforms + seed). Near-field
only (no room reverb): the supported deployment regime, and the regime the model was
shown to work in. These scenes are synthesized at eval time from the frozen fixture
clips, not committed.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

SR = 16000
_F32 = npt.NDArray[np.float32]

# Episode composition defaults, chosen to resemble a real sustained crying spell.
FLOOR_LEVEL = 0.006  # quiet broadband nursery bed
_ONSET_S = 5.0  # cry begins here (leaves a quiet lead-in to measure latency from)
_TAIL_S = 12.0  # trailing floor after the episode
_BURST_SNR_DB = (8.0, 18.0)  # a cry near a close mic sits above the floor
_BURST_GAP_S = (0.1, 0.6)  # short breaths between bursts
_PITCH_JITTER = 0.06  # +-6% resample per burst ~ within-infant F0/tempo variation
_GAIN_JITTER = 0.15


def _floor(n: int, rng: np.random.Generator, level: float = FLOOR_LEVEL) -> _F32:
    return (level * rng.standard_normal(n)).astype(np.float32)


def _mix_at(dst: _F32, src: _F32, start: int, snr_db: float) -> None:
    """Add ``src`` into ``dst`` at sample ``start``, scaled to ``snr_db`` above the
    floor energy already present under it."""
    m = min(len(dst) - start, len(src))
    if m <= 0:
        return
    seg = src[:m]
    floor_rms = float(np.sqrt(np.mean(dst[start : start + m] ** 2) + 1e-9))
    seg_rms = float(np.sqrt(np.mean(seg**2) + 1e-9))
    gain = (floor_rms * 10.0 ** (snr_db / 20.0)) / (seg_rms + 1e-9)
    dst[start : start + m] += (gain * seg).astype(np.float32)


def _vary(clip: _F32, rng: np.random.Generator, pitch_jitter: float) -> _F32:
    """A within-infant variation of a cry burst: small resample (F0/tempo shift) +
    gain jitter. Decorrelates successive bursts the way a real crying spell does,
    without pretending they are independent infants."""
    out = clip
    if pitch_jitter > 0:
        import scipy.signal

        ratio = 1.0 + pitch_jitter * (rng.random() * 2 - 1)
        num = max(1, int(round(1000 * ratio)))
        out = scipy.signal.resample_poly(clip, num, 1000).astype(np.float32)
    return (out * (1.0 + _GAIN_JITTER * (rng.random() * 2 - 1))).astype(np.float32)


def cry_episode(
    cry_clips: list[_F32],
    length_s: float,
    rng: np.random.Generator,
    *,
    mode: str = "within_infant",
) -> tuple[_F32, float]:
    """A sustained cry episode of ~``length_s`` on a noise floor; returns
    ``(waveform, onset_seconds)``.

    ``mode`` sets how correlated the bursts are — the honest bracket:
    ``"single"`` loops one infant's clip (fully correlated, pessimistic floor),
    ``"within_infant"`` adds pitch/tempo variation to one infant (the realistic
    model), ``"varied"`` draws from all infants (diverse ceiling)."""
    total = int((length_s + _ONSET_S + _TAIL_S) * SR)
    wave = _floor(total, rng)
    onset = int(_ONSET_S * SR)
    base = cry_clips[int(rng.integers(len(cry_clips)))]
    pitch = 0.0 if mode == "single" else _PITCH_JITTER
    t, end = onset, onset + int(length_s * SR)
    while t < end:
        clip = (
            base
            if mode in ("single", "within_infant")
            else cry_clips[int(rng.integers(len(cry_clips)))]
        )
        burst = _vary(clip, rng, pitch)
        _mix_at(wave, burst, t, float(rng.uniform(*_BURST_SNR_DB)))
        t += len(burst) + int(rng.uniform(*_BURST_GAP_S) * SR)
    return wave, _ONSET_S


def confuser_night(
    confuser_pools: dict[str, list[_F32]],
    minutes: float,
    rng: np.random.Generator,
    *,
    density: float = 0.35,
    snr_db: tuple[float, float] = (3.0, 18.0),
) -> _F32:
    """A cry-free stretch of ~``minutes`` minutes: confuser clips placed at ``density``
    occupancy on the floor (TV/pets/white-noise/speech running through the night)."""
    total = int(minutes * 60 * SR)
    wave = _floor(total, rng)
    labels = list(confuser_pools)
    t = 0
    while t < total:
        if rng.random() < density:
            pool = confuser_pools[labels[int(rng.integers(len(labels)))]]
            clip = pool[int(rng.integers(len(pool)))]
            _mix_at(wave, clip, t, float(rng.uniform(*snr_db)))
            t += len(clip)
        else:
            t += int(rng.uniform(2.0, 8.0) * SR)
    return wave
