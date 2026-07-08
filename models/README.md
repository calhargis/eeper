# models — ML model manifest & fetch tooling

Pretrained models are **versioned artifacts fetched at first run**, never baked
into images or committed to the repo. `manifest.json` is the source of truth:
name, version, URL, and SHA-256 checksum for each model. The fetch tool
(`eeper.insight.modelfetch`, M2.3) downloads and checksum-verifies them, refusing
tampered/corrupt files. Downloaded artifacts live under `models/cache/` (or the
insight container's model cache) and are git-ignored.

## Cry detection (M2.3)

`yamnet-classifier` is a **split** YAMNet: only the classifier body (log-mel patch
→ 521 AudioSet scores; infant-cry = class index 20) is shipped as ONNX. eeper
computes the log-mel frontend itself in versioned NumPy
(`server/eeper/insight/frontend.py`), so preprocessing is ordinary tested code that
runs identically on amd64/arm64 and every future model reuses the same input. This
also avoids the fragile in-graph STFT/RFFT ops that break a whole-model conversion.

- **Provenance / license:** derived from canonical YAMNet (github.com/tensorflow/
  models, `research/audioset/yamnet`), **Apache-2.0** (AGPL-compatible). Weights:
  `https://storage.googleapis.com/audioset/yamnet.h5`.
- **Reproduce** the artifact with [`convert_yamnet.py`](convert_yamnet.py) (pins
  Keras-2 so `tf2onnx` converts cleanly). It prints the exact bytes + SHA-256 to
  pin in `manifest.json`.
- **Hosting:** the converted ONNX is a GitHub Release asset
  (`models-yamnet-v1`); the frontend must match `frontend_version` in the frontend
  module, and the artifact reproduces reference waveform-in YAMNet to < 1e-5.
