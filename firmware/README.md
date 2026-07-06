# firmware — sensor node firmware

Firmware for the optional ESP32 sensor nodes that publish to the MQTT bus.

Planned (Phase 3, M3.2):

- **ESPHome configs** — 60 GHz mmWave (presence, movement index, respiration
  estimate) and PIR nodes.
- **MicroPython fallback template** — for boards/sensors ESPHome doesn't cover.

Node payloads validate against the sensor contract schema (a small MQTT JSON
contract), and configs are compile-checked in CI.
