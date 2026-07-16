# Thermal capture node (post-v1, optional)

The thermal node is an **optional, experimental** Pi capture node (Phase 6, [§4.5](./MASTER_PLAN.md#45-thermal-post-v1-optional)) that reads an **MLX90640** 32×24 thermal array over I²C and publishes it to eeper as an ordinary paired device. It pairs, publishes, and revokes through the exact same MQTT flow as any sensor node — no special setup on the server.

Surface temperatures only. The node publishes presence/warm-region **features** (the only signal fusion uses) plus the full grid for characterization/debug — **never a body-temperature readout** ([§2](./MASTER_PLAN.md#2-safety--regulatory-stance)).

## Hardware

- A Raspberry Pi (4 or 5) — see [hardware.md](./hardware.md).
- An **MLX90640** breakout (55° or 110° FOV) wired to the Pi's I²C: `VIN→3V3`, `GND→GND`, `SDA→GPIO2`, `SCL→GPIO3`.
- Enable I²C on the Pi (`raspi-config` → Interface Options → I²C), then confirm the sensor at address `0x33` (`i2cdetect -y 1`).

## Pair the node

In the eeper web UI, open **Devices → Pair**, choose kind **thermal**, and name it (e.g. `thermal-crib`). You get a one-time credential:

- MQTT username — `dev-<id>`
- MQTT password — shown **once**; save it now
- topic prefix — `eeper/dev/<id>/`

Also copy your deployment's MQTT CA (`deploy/mosquitto/certs/mqtt-ca.crt`) onto the node — the broker is TLS-only.

## Install & run

On the Pi (Python 3.12+):

```bash
pip install "eeper[thermal]"        # the MLX90640 driver + Blinka (hardware extra)
```

Configure via the environment, then run the node:

```bash
export EEPER_THERMAL_DEVICE_ID=<id>          # the numeric id from the Devices screen
export EEPER_MQTT_HOST=eeper.local           # your eeper host
export EEPER_MQTT_TLS_PORT=8883
export EEPER_MQTT_CA_CERT=/path/to/mqtt-ca.crt
export EEPER_MQTT_USERNAME=dev-<id>          # defaults to dev-<id> if unset
export EEPER_MQTT_PASSWORD=<paste-the-once-shown-password>
# optional tuning:
export EEPER_THERMAL_GRID_HZ=4               # grid rate, capped at 4 Hz
export EEPER_THERMAL_FEATURES_INTERVAL_S=1   # low-rate features cadence

python -m eeper.thermal
```

The node publishes the grid to `eeper/dev/<id>/thermal` (2–4 Hz) and the derived features to `eeper/dev/<id>/thermal_features` (low-rate). A checksum/read failure degrades health rather than emitting a bad grid; the node reads **offline** in the Devices view if it stops publishing.

## Notes

- The `thermal` extra (the CircuitPython driver) is **not** part of the eeper server image — it installs only on the node, so it never enters the audited runtime dependencies.
- This input is **experimental**: whether thermal presence earns a place in the sleep/wake fusion is decided by the M6.2 characterization gate. Until then the node is a supported, standalone input whose data lands in `thermal_features` for characterization.
