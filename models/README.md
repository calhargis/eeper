# models — ML model manifest & fetch tooling

Pretrained models are **versioned artifacts fetched at first run**, never baked
into images or committed to the repo. `manifest.json` is the source of truth:
name, version, URL, and SHA-256 checksum for each model. The fetch tool
(Phase 2, M2.3) downloads and checksum-verifies them, refusing tampered files.

`manifest.json` is empty until the first model lands (a YAMNet-class audio-event
model for cry detection). Downloaded artifacts live under `models/cache/` and
are git-ignored.
