"""Thermal input (Phase 6, §4.5) — the node-side capture/publish logic.

Pure, hardware-free modules so the whole publish path is testable without an MLX90640:
:mod:`features` derives the §4.5 presence/warm-region features from a raw grid,
:mod:`publisher` is the read → validate → rate-limit → emit loop, and :mod:`sensor`
defines the sensor interface plus a synthetic grid renderer. The real MLX90640 driver +
paho MQTT wiring is an optional entrypoint (M6.1 slice 2), not imported here.

Surface temperatures only — presence/trend features, never a body-temperature readout (§2).
"""
