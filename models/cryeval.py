"""Audio-nudge quality gate over the frozen fixtures-v1 eval split (M2.3, crit 2).

M2.3 ships two audio signals, and the gate reflects their honestly-measured standing:

* **Sound level (primary, gated).** eeper's v1 nudge is sustained loudness above the
  quiet nursery floor — the robust, model-free behaviour of every classic audio baby
  monitor. Its gate is PRODUCT-DERIVED, on EPISODES and NIGHTS, each threshold traced
  to a parent consequence:
    - *episode recall* — a sustained crying spell is unmistakably louder than a quiet
      floor, so the detector must catch essentially all of them.
    - *latency* — a nudge is only useful while the baby is still crying.
    - *false events on a quiet night* — a quiet nursery must not manufacture wake-ups.
    - *continuous-noise absorption* — a white-noise machine (steady ambient) must be
      absorbed by the adaptive baseline, not nudge all night.

* **Cry classification (experimental, ratcheted — not gated).** Pretrained YAMNet can't
  tell a cry from a bark/loud-TV to a first-class bar: on this split it reaches ~0.80
  near-field / ~0.76 far-field window recall and ~0.85 near-field episode recall at a
  false-nudge-safe point — real, but short of a first-class ~0.95. So it ships off by
  default, and its window accuracy is recorded as RATCHET BASELINES: CI fails on a
  regression, but they don't block on an aspirational absolute.

  (The ~0.85 is the aggregate episode recall on the fixture episode mix — distinct from
  M2.3's ~0.70 single-infant worst-case ceiling, where within-infant window errors
  correlate.) M2.5 promoted "train a model to unlock first-class cry." A reproducible
  de-risk (recorded in the M2.5 PR) tested exactly that and found the wall is the CORPUS,
  not the model: a trained head (logistic + MLP, over both the 521-class AudioSet scores
  and the 1024-d YAMNet embeddings, balanced + near/far augmented) over the FULL donateacry
  corpus does NOT beat this pretrained scorer at any false-nudge-safe point (best trained
  near-field recall ~0.47 vs ~0.84 pretrained on the same split) — the binding confuser is
  cry-vs-animal, where the hand-tuned animal-band suppression is a strong inductive bias a
  naive head can't recover on held-out infants. The split was device-disjoint on
  donateacry's per-upload UUID (a reasonable but imperfect infant proxy); any residual
  infant leakage would only flatter the trained head, which still lost. donateacry is the
  only cry source (457 clips, one narrow near-field dataset, no guaranteed infant-level
  id); FSD50K supplies confusers only. So first-class cry is gated on a bigger, more
  diverse corpus — with guaranteed infant-disjoint splits and real far-field, the M2.6
  milestone — not on a training trick. These floors are M2.6's starting line.

Everything is seeded, deterministic, and pinned to the fixture version. Run
``python models/cryeval.py gate`` (assert) or ``calibrate`` (sweep). Needs the server
+ fixtures packages importable, onnxruntime, scipy, soundfile, and (far-field cry
ratchet only) pyroomacoustics.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import numpy.typing as npt
import scipy.signal
import soundfile as sf

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO / "fixtures"))

from eeper.insight import cry, frontend, sound  # noqa: E402
from eeper.insight.modelfetch import ensure_models  # noqa: E402
from eeper_fixtures import generate, longform  # noqa: E402
from eeper_fixtures.fetch import fetch_clip  # noqa: E402
from eeper_fixtures.manifest import ClipSpec, Gen, load_manifest  # noqa: E402

SAMPLE_RATE = 16000
CONFUSER_CATEGORIES = ("speech", "music_tv", "pets", "whitenoise_lullaby")
PATCH_HOP_S = 0.48
# Fixed integer ids for RNG seeding — NEVER Python's hash() (randomized per process
# via PYTHONHASHSEED, which would make the deterministic gate/ratchet drift).
_LABEL_ID = {"cry": 0, "speech": 1, "music_tv": 2, "pets": 3, "whitenoise_lullaby": 4}
_REGIME_ID = {"near": 1, "far": 2}
_MODE_ID = {"single": 1, "within_infant": 2, "varied": 3}
EPISODE_LENGTH_S = 40.0  # a "sustained" spell (>= 20-30 s of crying)
N_EPISODES = 40
NIGHT_MINUTES = 60.0  # measured per night, scaled x8 to a night
N_NIGHTS = 4
_F32 = np.float32

# ── sound-level product gate (PRIMARY; derivations above) ──────────────────────────
SOUND_EPISODE_RECALL_FLOOR = 0.90  # >= 90% of sustained cry spells caught (measured ~0.95)
SOUND_LATENCY_CEILING_S = 10.0  # median onset->nudge (measured ~2 s)
SOUND_QUIET_FALSE_CEILING = 1.0  # per 8 h, quiet nursery floor only (measured 0)
# A white-noise machine is one switch-on (one onset the baseline then absorbs), not a
# rate — so this is a RAW per-night event count, not scaled (measured ~1).
SOUND_CONTINUOUS_NOISE_RAW_CEILING = 3.0

# ── cry-classifier window ratchet baselines (EXPERIMENTAL) ──────────────────────────
# A ratchet, not a product bar: each floor sits just below the honestly-measured value
# on this frozen deterministic split (a small headroom absorbs cross-version numeric
# drift), so CI fails on a genuine regression and the floor only ever moves UP. These
# were ratcheted up here from the M2.3 starting line (0.75/0.12 near, 0.55/0.20 far)
# after the M2.5 de-risk: the far "collapse" was largely a shared-threshold artifact,
# and — the de-risk's headline — a trained head does NOT beat this pretrained scorer on
# the donateacry-only corpus, so first-class cry is gated on a corpus, not a model (see
# the module docstring + M2.6 in IMPLEMENTATION_PLAN.md). Numbers are per gate run,
# fully deterministic (PYTHONHASHSEED=0 + fixed seeds).
K_WINDOW = 3
_SNR_CRY = (5.0, 20.0)
_SNR_CONFUSER = (0.0, 15.0)
_MIN_SECONDS = 3.0
NEARFIELD_WINDOW_RECALL_FLOOR = 0.78  # measured 0.800
NEARFIELD_WINDOW_MAXFPR_CEILING = 0.08  # measured 0.052 (binding confuser: pets)
FARFIELD_WINDOW_RECALL_FLOOR = 0.72  # measured 0.758
FARFIELD_WINDOW_MAXFPR_CEILING = 0.11  # measured 0.083 (binding confuser: pets)


def _load_16k_mono(data: bytes) -> npt.NDArray[np.float32]:
    audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if sr != SAMPLE_RATE:
        audio = scipy.signal.resample_poly(audio, SAMPLE_RATE, sr)
    return np.ascontiguousarray(audio, dtype=_F32)


def _clip_bytes(clip: ClipSpec, cache_dir: Path) -> bytes:
    if clip.generated:
        assert clip.gen is not None
        return generate.generate_clip(clip.gen)
    return fetch_clip(clip, cache_dir).read_bytes()


def load_clips(manifest_path: Path, cache_dir: Path, splits: tuple[str, ...]):
    manifest = load_manifest(manifest_path)
    out: dict[str, dict[str, list[npt.NDArray[np.float32]]]] = {s: defaultdict(list) for s in splits}
    for clip in manifest.clips:
        if clip.split not in splits:
            continue
        label = "cry" if clip.role == "fg_cry" else clip.category
        if label not in ("cry", *CONFUSER_CATEGORIES):
            continue
        out[clip.split][label].append(_load_16k_mono(_clip_bytes(clip, cache_dir)))
    for split, seed in (("dev", 100), ("eval", 200)):
        if split in splits:
            out[split]["whitenoise_lullaby"] = [
                _load_16k_mono(generate.generate_clip(Gen("white_noise" if k % 2 else "lullaby", seed + k, 3.0, 0.2)))
                for k in range(12)
            ]
    return {s: dict(d) for s, d in out.items()}


# ── sound-level scoring ─────────────────────────────────────────────────────────
def loudness_stream(wave: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
    n = len(wave) // SAMPLE_RATE
    return np.array([sound.window_loudness_dbfs(wave[i * SAMPLE_RATE : (i + 1) * SAMPLE_RATE]) for i in range(n)])


def sound_events(stream, cfg: sound.SoundLevelConfig) -> list[int]:
    det = sound.SoundLevelDetector(cfg)
    return [i for i, x in enumerate(stream) if det.update(float(x)) == "elevated"]


def sound_episode_metrics(episode_streams, cfg):
    detected, latencies = 0, []
    for stream, onset_s in episode_streams:
        fires = [f for f in sound_events(stream, cfg) if f + 1 >= onset_s]
        if fires:
            detected += 1
            latencies.append(fires[0] - onset_s)
    recall = detected / len(episode_streams) if episode_streams else 0.0
    return recall, (float(np.median(latencies)) if latencies else float("nan"))


def sound_false_per_8h(night_streams, cfg) -> float:
    return float(np.mean([len(sound_events(s, cfg)) * (8 * 60 / NIGHT_MINUTES) for s in night_streams]))


# ── cry classifier: window ratchet + episode transparency ─────────────────────────
def patch_bands(wave, clf: cry.CryClassifier, chunk_s: float = 120.0):
    bands, centers = [], []
    chunk = int(chunk_s * SAMPLE_RATE)
    for start in range(0, max(1, len(wave)), chunk):
        patches = frontend.log_mel_patches(wave[start : start + chunk])
        if patches.shape[0] == 0:
            continue
        bands.append(clf.patch_bands(patches))
        centers.append((np.arange(patches.shape[0]) + 1) * PATCH_HOP_S + start / SAMPLE_RATE)
    if not bands:
        return np.zeros(0, _F32), np.zeros(0)
    return np.concatenate(bands), np.concatenate(centers)


def hop_scores(bands, centers, dur_s, context_s):
    nhop = int(dur_s)
    out = np.full(nhop, -1e9)
    for i in range(nhop):
        end = i + 1
        mask = (centers > end - context_s) & (centers <= end)
        if mask.any():
            out[i] = bands[mask].max()
    return out


def cry_episode_metrics(episode_bands, context_s, config):
    detected, latencies = 0, []
    for bands, centers, onset_s, dur_s in episode_bands:
        det = cry.CryEpisodeDetector(config)
        onsets = [i for i, s in enumerate(hop_scores(bands, centers, dur_s, context_s)) if det.update(float(s)) == "crying"]
        hit = [o for o in onsets if o + 1 >= onset_s]
        if hit:
            detected += 1
            latencies.append(hit[0] - onset_s)
    return (detected / len(episode_bands) if episode_bands else 0.0), (float(np.median(latencies)) if latencies else float("nan"))


def _near_field(clip, snr_db, rng):
    n = max(len(clip), int(_MIN_SECONDS * SAMPLE_RATE))
    buf = np.zeros(n, _F32)
    buf[: len(clip)] = clip[:n]
    noise = rng.standard_normal(n).astype(_F32)
    noise *= (np.sqrt(np.mean(buf**2) + 1e-9) / (np.sqrt(np.mean(noise**2) + 1e-9))) / (10 ** (snr_db / 20))
    return buf + noise


def _far_field(clip, snr_db, rng):
    import pyroomacoustics as pra

    dims = np.array([rng.uniform(3.0, 5.0), rng.uniform(2.5, 4.0), 2.7])
    e_abs, max_order = pra.inverse_sabine(rng.uniform(0.3, 0.6), dims)
    room = pra.ShoeBox(dims.tolist(), fs=SAMPLE_RATE, materials=pra.Material(e_abs), max_order=min(max_order, 12))
    room.add_source([rng.uniform(0.6, dims[0] - 0.6), rng.uniform(0.6, dims[1] - 0.6), rng.uniform(0.3, 0.7)], signal=clip.astype(np.float64))
    room.add_microphone(np.array([rng.uniform(0.6, dims[0] - 0.6), rng.uniform(0.6, dims[1] - 0.6), rng.uniform(1.6, 2.4)]).reshape(3, 1))
    room.simulate()
    rev = room.mic_array.signals[0].astype(_F32)
    rev = rev / (np.max(np.abs(rev)) + 1e-9) * 0.7
    noise = rng.standard_normal(len(rev)).astype(_F32)
    noise *= (np.sqrt(np.mean(rev**2) + 1e-9) / (np.sqrt(np.mean(noise**2) + 1e-9))) / (10 ** (snr_db / 20))
    return rev + noise


def cry_window_ratchet(clips, clf, regime, seed, threshold):
    synth = _near_field if regime == "near" else _far_field
    cry_scores, conf = [], {c: [] for c in CONFUSER_CATEGORIES}
    for label, waves in clips.items():
        lo, hi = _SNR_CRY if label == "cry" else _SNR_CONFUSER
        for i, w in enumerate(waves):
            for j in range(K_WINDOW):
                rng = np.random.default_rng([seed, _REGIME_ID[regime], _LABEL_ID[label], i, j])
                s = clf.score_waveform(synth(w, float(rng.uniform(lo, hi)), rng))
                (cry_scores if label == "cry" else conf[label]).append(s)
    cry_arr = np.array(cry_scores)
    recall = float((cry_arr >= threshold).mean()) if len(cry_arr) else 0.0
    fprs = {c: (float((np.array(v) >= threshold).mean()) if v else 0.0) for c, v in conf.items()}
    return recall, fprs


# ── scene builders ────────────────────────────────────────────────────────────────
def build_cry_episodes(cry_clips, mode, seed):
    out = []
    for e in range(N_EPISODES):
        rng = np.random.default_rng([seed, e, _MODE_ID[mode]])
        wave, onset_s = longform.cry_episode(cry_clips, EPISODE_LENGTH_S, rng, mode=mode)
        out.append((wave, onset_s))
    return out


def quiet_night(seed, minutes):
    rng = np.random.default_rng([seed])
    return (longform.FLOOR_LEVEL * rng.standard_normal(int(minutes * 60 * SAMPLE_RATE))).astype(_F32)


def continuous_noise_night(seed, minutes, level=0.03):
    """A white-noise-machine night: quiet floor for the first 3 min, then a steady loud
    bed switched on (a step the adaptive baseline must absorb after one onset)."""
    rng = np.random.default_rng([seed])
    n = int(minutes * 60 * SAMPLE_RATE)
    wave = (longform.FLOOR_LEVEL * rng.standard_normal(n)).astype(_F32)
    on = int(3 * 60 * SAMPLE_RATE)
    wave[on:] += (level * rng.standard_normal(n - on)).astype(_F32)
    return wave


def _classifier(cache_dir: Path) -> cry.CryClassifier:
    onnx = ensure_models(_REPO / "models" / "manifest.json", cache_dir)["yamnet-classifier"]
    return cry.CryClassifier(str(onnx))


def gate(manifest: Path, cache_dir: Path) -> int:
    data = load_clips(manifest, cache_dir, ("eval",))
    print("eval clips:", {k: len(v) for k, v in data["eval"].items()}, flush=True)
    failures: list[str] = []

    # ── PRIMARY: sound-level product gate (no model needed) ──
    episodes = build_cry_episodes(data["eval"]["cry"], "within_infant", 200)
    epi_loud = [(loudness_stream(w), onset) for w, onset in episodes]
    scfg = sound.config_for(sound.DEFAULT_SENSITIVITY)
    s_recall, s_lat = sound_episode_metrics(epi_loud, scfg)
    quiet = [loudness_stream(quiet_night(300 + s, NIGHT_MINUTES)) for s in range(N_NIGHTS)]
    q_false = sound_false_per_8h(quiet, scfg)
    cont = [loudness_stream(continuous_noise_night(400 + s, NIGHT_MINUTES)) for s in range(2)]
    c_raw = float(np.mean([len(sound_events(s, scfg)) for s in cont]))  # RAW per-night count
    print(f"SOUND-LEVEL: episode_recall={s_recall:.3f} median_latency={s_lat:.1f}s "
          f"quiet_false/8h={q_false:.2f} continuous_noise_events/night={c_raw:.2f}", flush=True)
    if s_recall < SOUND_EPISODE_RECALL_FLOOR:
        failures.append(f"sound episode recall {s_recall:.3f} < {SOUND_EPISODE_RECALL_FLOOR}")
    if not (s_lat <= SOUND_LATENCY_CEILING_S):
        failures.append(f"sound median latency {s_lat:.1f}s > {SOUND_LATENCY_CEILING_S}s")
    if q_false > SOUND_QUIET_FALSE_CEILING:
        failures.append(f"sound quiet-night false events {q_false:.2f} > {SOUND_QUIET_FALSE_CEILING}")
    if c_raw > SOUND_CONTINUOUS_NOISE_RAW_CEILING:
        failures.append(f"sound continuous-noise events/night {c_raw:.2f} > {SOUND_CONTINUOUS_NOISE_RAW_CEILING}")

    # ── EXPERIMENTAL: cry-classifier window ratchet baselines ──
    clf = _classifier(cache_dir)
    ccfg = cry.config_for(cry.DEFAULT_SENSITIVITY)
    nf_recall, nf_fprs = cry_window_ratchet(data["eval"], clf, "near", 41, ccfg.threshold)
    print(f"CRY window near-field (ratchet): recall={nf_recall:.3f} maxFPR={max(nf_fprs.values()):.3f} {nf_fprs}", flush=True)
    if nf_recall < NEARFIELD_WINDOW_RECALL_FLOOR:
        failures.append(f"near-field cry window recall {nf_recall:.3f} < ratchet {NEARFIELD_WINDOW_RECALL_FLOOR}")
    if max(nf_fprs.values()) > NEARFIELD_WINDOW_MAXFPR_CEILING:
        failures.append(f"near-field cry window maxFPR {max(nf_fprs.values()):.3f} > ratchet {NEARFIELD_WINDOW_MAXFPR_CEILING}")
    ff_recall, ff_fprs = cry_window_ratchet(data["eval"], clf, "far", 43, ccfg.threshold)
    print(f"CRY window far-field  (ratchet): recall={ff_recall:.3f} maxFPR={max(ff_fprs.values()):.3f} {ff_fprs}", flush=True)
    if ff_recall < FARFIELD_WINDOW_RECALL_FLOOR:
        failures.append(f"far-field cry window recall {ff_recall:.3f} < ratchet {FARFIELD_WINDOW_RECALL_FLOOR}")
    if max(ff_fprs.values()) > FARFIELD_WINDOW_MAXFPR_CEILING:
        failures.append(f"far-field cry window maxFPR {max(ff_fprs.values()):.3f} > ratchet {FARFIELD_WINDOW_MAXFPR_CEILING}")

    # ── TRANSPARENCY: why cry is experimental (reported, not gated) ──
    epi_bands = [(*patch_bands(w, clf), onset, len(w) / SAMPLE_RATE) for w, onset in episodes]
    cry_recall, cry_lat = cry_episode_metrics(epi_bands, cry.CONTEXT_SECONDS, ccfg)
    print(f"CRY episode (experimental, reported): recall={cry_recall:.3f} latency={cry_lat:.1f}s "
          f"(< sound-level; why cry stays off-by-default -> M2.6 corpus)", flush=True)

    if failures:
        print("\nQUALITY GATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nquality gate PASSED (sound-level product contract + cry ratchets)")
    return 0


def calibrate(manifest: Path, cache_dir: Path) -> None:
    data = load_clips(manifest, cache_dir, ("eval",))
    episodes = build_cry_episodes(data["eval"]["cry"], "within_infant", 200)
    epi_loud = [(loudness_stream(w), onset) for w, onset in episodes]
    quiet = [loudness_stream(quiet_night(300 + s, NIGHT_MINUTES)) for s in range(N_NIGHTS)]
    cont = [loudness_stream(continuous_noise_night(400 + s, NIGHT_MINUTES)) for s in range(2)]
    print("sound-level sweep (recall / latency / quiet-false / continuous-false):", flush=True)
    for sens in (0.2, 0.35, 0.5, 0.65, 0.8):
        cfg = sound.config_for(sens)
        r, lat = sound_episode_metrics(epi_loud, cfg)
        q = sound_false_per_8h(quiet, cfg)
        c = sound_false_per_8h(cont, cfg)
        print(f"  sens={sens} margin={cfg.margin_db:.1f}dB: recall={r:.3f} lat={lat:.0f}s quiet={q:.2f} continuous={c:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="audio-nudge quality gate")
    parser.add_argument("mode", choices=("gate", "calibrate"))
    parser.add_argument("--manifest", type=Path, default=_REPO / "fixtures" / "manifest.json")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/cryeval-cache"))
    args = parser.parse_args()
    if args.mode == "calibrate":
        calibrate(args.manifest, args.cache)
        return 0
    return gate(args.manifest, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
