"""Cry detection: pet-suppressed window scoring over the frozen YAMNet ONNX plus a
temporal episode detector for the live audio stream (M2.3).

The split model is: eeper's versioned log-mel frontend (:mod:`frontend`) turns a
16 kHz mono waveform into ``[num_patches, 96, 64]`` patches, and the checksum-pinned
YAMNet classifier ONNX turns those into ``[num_patches, 521]`` AudioSet scores.

**Window score.** For one short audio window, the cry confidence is the peak, over
its patches, of the summed AudioSet cry band — "Crying, sobbing" (19), "Baby cry,
infant cry" (20), "Whimper" (21), "Wail, moan" (22) — minus a suppression term on the
animal band (dog/cat/bird/livestock classes). Pets are the dominant confuser: they
trigger the cry labels *and* the animal labels, so subtracting the animal activation
cuts pet false positives while barely touching real cries.

**Episode detection.** A single window is a weak, noisy signal — pretrained YAMNet
detects only ~85% of individual cries and confusers spike occasionally. What parents
actually care about is a *sustained crying spell*, and what destroys their trust is a
false wake-up. So the live detector doesn't nudge on one window: it runs k-of-n window
voting with a refractory period (the same hysteresis discipline the motion state
machine applies to frame scores), which drives per-window false positives to ~0 per
night while still firing within seconds of a real episode's onset. Window-level
metrics are the classifier's raw quality (recorded as ratchet baselines in the M2.3
gate); the episode metrics are the product contract.

Scope + honesty: validated for the near-field regime (a close/crib-mounted camera).
It detects sustained crying within seconds; a brief isolated whimper may not trigger a
nudge (by design — that is not a wake-the-parent event), and cries from infants whose
vocalizations are atypical to YAMNet can be missed. Reverberant far-field capture
degrades it further; both are measured, named, and ratcheted in the gate. M2.5's de-risk
showed a trained head can't lift this on the corpus that exists (it doesn't beat the
pretrained scorer — the ceiling is the corpus, not the model), so cry stays experimental
+ off by default and first-class cry is gated on the M2.6 corpus expansion. This is an
awareness signal — "a cry was heard" — never a medical or distress readout.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import onnxruntime as ort

from eeper.insight import frontend

# AudioSet cry band (summed per patch, peaked over the window's patches).
CRY_CLASS_INDICES: tuple[int, ...] = (19, 20, 21, 22)

# AudioSet animal band, derived from YAMNet's class map (dog/cat/bird/livestock/rodent
# labels; chewing/pizzicato/chirp-tone keyword hits excluded). Pets trigger these
# alongside the cry band, so the window score subtracts a fraction of the animal-band
# peak to suppress pet false positives.
ANIMAL_CLASS_INDICES: tuple[int, ...] = (
    67,
    68,
    69,
    70,
    71,
    72,
    73,
    74,
    75,
    76,
    77,
    78,
    79,
    80,
    81,
    85,
    95,
    103,
    104,
    106,
    107,
    108,
    116,
    117,
    118,
)
ANIMAL_SUPPRESS = 0.5  # weight on the animal-band peak subtracted from the cry band

_F32 = np.float32


# Seconds of audio the live scorer feeds per window score (the rolling context it
# runs the frontend over). One patch is 0.96 s, so ~2 s gives a few patches to peak
# over; calibrated with the detector params on the dev split.
CONTEXT_SECONDS = 2.0


def patch_cry_bands(scores: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """``[num_patches, 521]`` AudioSet scores -> per-patch pet-suppressed cry band
    (cry-band sum - ANIMAL_SUPPRESS * animal-band peak). Empty for no patches."""
    if scores.ndim != 2 or scores.shape[0] == 0:
        return np.empty(0, dtype=_F32)
    cry = scores[:, list(CRY_CLASS_INDICES)].sum(axis=1)
    animal = scores[:, list(ANIMAL_CLASS_INDICES)].max(axis=1)
    return (cry - ANIMAL_SUPPRESS * animal).astype(_F32)


def window_score(scores: npt.NDArray[np.float32]) -> float:
    """One window's cry confidence: peak over its patches of the pet-suppressed cry
    band. 0.0 for no patches. Not clamped — the raw margin is what the detector
    thresholds and the sensitivity knob calibrate against."""
    bands = patch_cry_bands(scores)
    return float(bands.max()) if bands.size else 0.0


class CryClassifier:
    """Frozen YAMNet classifier body wrapped for pet-suppressed window scoring. One
    session per process; the single insight audio scorer is the only caller."""

    def __init__(self, onnx_path: str, *, providers: list[str] | None = None) -> None:
        self._session = ort.InferenceSession(
            onnx_path, providers=providers or ["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def patch_bands(self, patches: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """Per-patch pet-suppressed cry band for a patch batch (the primitive the
        eval's context-max scoring and :meth:`score_patches` share)."""
        if patches.shape[0] == 0:
            return np.empty(0, dtype=_F32)
        scores = self._session.run(None, {self._input_name: patches.astype(_F32)})[0]
        return patch_cry_bands(scores)

    def score_patches(self, patches: npt.NDArray[np.float32]) -> float:
        bands = self.patch_bands(patches)
        return float(bands.max()) if bands.size else 0.0

    def score_waveform(self, waveform: npt.NDArray[np.float32]) -> float:
        """16 kHz mono float32 window (the rolling context) -> pet-suppressed cry
        confidence for that window."""
        return self.score_patches(frontend.log_mel_patches(np.asarray(waveform, dtype=_F32)))

    def score_pcm(self, pcm: bytes) -> float:
        return self.score_waveform(frontend.pcm_to_waveform(pcm))


# ── sensitivity -> window threshold ────────────────────────────────────────────
# Default sensitivity + the threshold band it maps to, calibrated on the frozen
# fixtures-v1 dev split (see models/cryeval.py) so the episode gate is met on the
# eval split. Sensitivity is a 0..1 user knob: higher => lower threshold => the k-of-n
# vote trips more easily => more sensitive (and a looser false-nudge budget).
DEFAULT_SENSITIVITY = 0.5
_THRESHOLD_AT_MIN_SENS = 0.15  # sensitivity 0.0 (least sensitive)
_THRESHOLD_AT_MAX_SENS = 0.02  # sensitivity 1.0 (most sensitive)


def window_threshold_for(sensitivity: float) -> float:
    """Map a 0..1 sensitivity knob to a per-window vote threshold (log-spaced across
    the small operating range so the knob stays usable). Clamped to [0, 1]."""
    s = min(1.0, max(0.0, sensitivity))
    lo, hi = _THRESHOLD_AT_MAX_SENS, _THRESHOLD_AT_MIN_SENS
    return float(hi * (lo / hi) ** s)


# ── temporal episode detector (k-of-n voting) ──────────────────────────────────
# Calibrated on the dev split against the product contract (episode recall vs the
# false-nudge budget). One window ~= 1 s (the audio ring's window duration).
VOTE_WINDOW = 5  # n: votes are counted over the last n windows
VOTE_COUNT = 3  # k: >= k of the last n windows above threshold -> crying
REFRACTORY_WINDOWS = 20  # min windows between episode onsets (anti-repeat-nudge)


@dataclass(frozen=True)
class CryDetectorConfig:
    threshold: float
    vote_window: int = VOTE_WINDOW
    vote_count: int = VOTE_COUNT
    refractory_windows: int = REFRACTORY_WINDOWS


def config_for(sensitivity: float) -> CryDetectorConfig:
    return CryDetectorConfig(threshold=window_threshold_for(sensitivity))


class CryEpisodeDetector:
    """k-of-n window voting with a refractory period over the window-score stream.

    Emits ``"crying"`` on the rising edge (a sustained episode begins) and ``"quiet"``
    on the falling edge. The refractory blocks a second onset too soon after the last,
    so one crying spell is one nudge, not a burst. :meth:`revert` undoes a transition
    whose durable write failed, so the DB and the published state never diverge (the
    same contract the movement state machine honors)."""

    def __init__(self, config: CryDetectorConfig, initial: str = "quiet") -> None:
        self._cfg = config
        self.state = initial
        self._votes: deque[int] = deque(maxlen=config.vote_window)
        self._tick = 0
        self._last_onset_tick: int | None = None

    @property
    def last_onset_tick(self) -> int | None:
        return self._last_onset_tick

    def revert(self, state: str, votes: list[int], last_onset_tick: int | None) -> None:
        self.state = state
        self._votes = deque(votes, maxlen=self._cfg.vote_window)
        self._last_onset_tick = last_onset_tick

    def snapshot(self) -> tuple[str, list[int], int | None]:
        """The pre-update state to hand :meth:`revert` if the durable write fails."""
        return self.state, list(self._votes), self._last_onset_tick

    def update(self, window_confidence: float) -> str | None:
        """Feed one window's cry confidence (called ~once per second). Returns the new
        state on a transition, else ``None``. ``_tick`` advances every call."""
        self._tick += 1
        self._votes.append(1 if window_confidence >= self._cfg.threshold else 0)
        above = sum(self._votes) >= self._cfg.vote_count
        if self.state == "quiet" and above:
            last = self._last_onset_tick
            if last is not None and (self._tick - last) < self._cfg.refractory_windows:
                return None  # too soon after the last onset — hold (anti-repeat-nudge)
            self.state = "crying"
            self._last_onset_tick = self._tick
            return "crying"
        if self.state == "crying" and not above:
            self.state = "quiet"
            return "quiet"
        return None
