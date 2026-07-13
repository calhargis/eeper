# eeper fusion layer (M3.3)

Derives **sleep/wake** and **calm/distressed** states — and consolidated **sleep
sessions** — from the per-epoch outputs of every registered extractor (camera motion,
mmWave/PIR movement + presence, sound, cry). Awareness states only; never a medical,
diagnostic, or vital-sign readout.

Pure stdlib and streaming, so the exact code that CI replays against labeled fixture
nights is the code that runs live on a Pi.

## Pipeline

```
raw samples ──► epochs.featurize ──► state.FusionStateMachine ──► sessions.extract
 (per modality)   30 s bins,          activity → median smooth      consolidated
                  gap-tolerant        → hysteresis + sustain         sleep periods
                                      → ≥2-signal distress
```

- **`epochs.py`** — bins each modality's irregular samples onto the 30 s grid (mean for
  continuous signals, max for presence/cry). A modality with no sample in an epoch is
  left `None`, so a dropped sensor degrades to the live inputs instead of faking zeros.
- **`state.py`** — the streaming state machine. Sleep/wake from a **median-smoothed**
  activity score (rejects isolated single-epoch twitches a naive threshold misreads as
  a wake) through a hysteresis band with a post-transition **sustain**; the wake sustain
  is below the 3-minute floor so every wake that long is caught. Distress needs
  **≥ 2 corroborating** signals and only while awake.
- **`sessions.py`** — bridges sub-break awakenings into consolidated sessions; back-dates
  each confirmed transition to when its run began, undoing the sustain's confirmation
  lag so session boundaries land within the ±2-min tolerance.
- **`synth.py`** — the seeded synthetic night generator (ground truth for the gates).
- **`replay.py`** — featurize → fuse → score, the harness the quality gate drives.

## Why synthetic ground truth

No public labeled infant multi-modal (video + radar + audio) sleep corpus exists, so the
[AUTO] replay gate scores the fusion against a **deterministic, seeded generator**: a
ground-truth sleep/wake + calm/distressed epoch sequence rendered into noisy sub-epoch
samples. Its difficulty is calibrated so a principled fusion clears the floors with
margin while a naive per-epoch threshold does not (sleep carries isolated visual
twitches; real wakes are distinguished by sustained activity, not amplitude alone). This
validates the fusion **logic**; real-world accuracy is the M3.3 [MANUAL] overnight-bench
criterion.

## Quality-gate envelope

Floors sit under the measured values with headroom (the ratchet pattern from the M2.5
cry gate) — a regression trips CI without flapping on noise. Measured over 60–80 seeds
on each of the four input subsets (all / video-only / radar-only / video+audio):

| Gate | Floor | Measured |
| --- | --- | --- |
| Sleep/wake epoch agreement (mean) | 0.90 | 0.963–0.968 |
| Wake ≥ 3 min recall | 0.95 | 1.000 |
| Session-count match | 0.90 | 0.963 |
| Session boundary within ±2 min | 0.85 | 0.892–0.968 |

The tests live in `server/tests/test_fusion_*.py`. Re-measure and ratchet the floors up
if the fusion improves; only lower a floor with a recorded justification.
