# firmware — sensor node firmware

Firmware for the optional ESP32 sensor nodes that publish to eeper's MQTT bus (M3.2).
A node is an *input* to eeper's sleep-state insights — never a medical or alarm device.

- **[`esphome/`](esphome/)** — reference ESPHome configs: a 24 GHz mmWave presence radar
  (HLK-LD2410) and a PIR motion node, plus a shared base package that handles the WiFi,
  SNTP clock, TLS-authenticated MQTT, and the wire contract.
- **[`micropython/`](micropython/)** — a MicroPython fallback template for boards or
  sensors ESPHome doesn't cover.
- **[`PROVISIONING.md`](PROVISIONING.md)** — flash a node and pair it with eeper.

Nodes publish the [`SensorMessage`](../server/eeper/api/schemas.py) contract as JSON to
`eeper/dev/{id}/{metric}`; the server validates every reading against that same schema.
CI validates and **compiles** the ESPHome configs and checks the emitted payloads
against the contract (`.github/workflows/firmware.yml`).

Licensed MIT (see [`LICENSE`](LICENSE)) — permissive so you can adapt node firmware for
your own hardware.
