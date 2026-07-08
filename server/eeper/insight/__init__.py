"""Insight engine (M2.1+).

Extracts signals from enabled camera streams. M2.1 ships the audio stage —
normalizing each camera's audio to 16 kHz mono PCM windows; M2.2 adds the frame
sampler, feature-extractor registry, and MQTT/state write path on top. Runs as
its own container (``python -m eeper.insight``), reusing the api image."""
