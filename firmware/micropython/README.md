# MicroPython fallback template

[`eeper_node.py`](eeper_node.py) is a minimal, dependency-light node for boards or
sensors [ESPHome](https://esphome.io) doesn't cover. It does what the ESPHome reference
configs do, by hand: join WiFi, sync the clock over SNTP, open a TLS MQTT connection
verified against the deployment CA, and publish `SensorMessage` JSON to
`eeper/dev/{id}/{metric}`.

It's a **template** — fill the `CONFIG` block and replace `read_metrics()` with your
sensor. Tested against MicroPython 1.22+ on ESP32.

## Use it

1. Pair the device in eeper (**Devices** screen) to get its id, `dev-<id>` username, and
   password — see [`../PROVISIONING.md`](../PROVISIONING.md).
2. Copy your deployment's `deploy/mosquitto/certs/mqtt-ca.crt` onto the board next to
   `eeper_node.py` (the `CA_CERT_PATH`).
3. Edit the `CONFIG` block (WiFi, `DEVICE_ID`, `MQTT_HOST`, `MQTT_USER`, `MQTT_PASSWORD`).
4. Implement `read_metrics()` to return `(metric, value, unit, quality)` tuples.
5. Copy `eeper_node.py` to the board (e.g. `mpremote cp eeper_node.py :`) and run it.

## Watch out for the same bus rules

- Publish **only** under `eeper/dev/<id>/` — anything else is ACL-denied and drops the
  connection. The template parks its last-will under `.../node/status`.
- The clock must be real before publishing — the template blocks on `sync_clock()` and
  offsets MicroPython's 2000-01-01 epoch to unix time; without it every reading is
  stamped 1970 and the node reads offline.
- MicroPython's TLS on ESP32 is more limited than desktop Python; verify your build
  supports `ssl_params` CA verification (`cadata`).

## Pulse-ox node (optional, insights-only)

[`eeper_pulseox_node.py`](eeper_pulseox_node.py) is the reference for an **optional**
pulse-ox node (e.g. an ESP32 + MAX30102 breakout). ESPHome has no MAX3010x heart-rate/SpO2
component, so pulse-ox uses this MicroPython path rather than the ESPHome one. It reuses
the same join / SNTP / TLS-MQTT plumbing but publishes the richer pulse-ox contract to
`eeper/dev/{id}/pulseox`:

```json
{ "ts": 1783900000.0, "hr": 124.0, "spo2": 98.0, "perfusion": 3.8, "quality": 0.82 }
```

- **`quality` (0..1) is mandatory and must be honest.** The server discards
  low-confidence samples rather than store misleading data, so inflating quality just
  gets bad samples dropped. Fill in `sample_quality()` / `read_pulseox()` for your driver.
- eeper is **not** a vital-sign monitor and this node is **not** a medical device — the
  numbers feed sleep-trend context and fusion arousal features only, never a readout or
  alarm. Consumer optical sensors are not medical-grade; follow safe-sleep guidance.
- Pulse-ox stays fully inert until an operator enables the pulse-ox profile **and** an
  admin acknowledges the in-app disclaimer, regardless of what a node publishes.
- Keep raw optical data on-device — only the derived `hr`/`spo2`/`perfusion`/`quality`
  ever leave the node.
