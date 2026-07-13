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
