# ESPHome reference nodes

Reference [ESPHome](https://esphome.io) configs for eeper sensor nodes. Each publishes
the eeper sensor contract (`SensorMessage` JSON) to `eeper/dev/{id}/{metric}` over
TLS-authenticated MQTT.

| File | Node | Metrics |
| --- | --- | --- |
| [`eeper-mmwave.yaml`](eeper-mmwave.yaml) | HLK-LD2410 24 GHz radar | `presence` (0/1), `movement` (0–100 index) |
| [`eeper-pir.yaml`](eeper-pir.yaml) | PIR (AM312 / HC-SR501) | `movement` (0/1) |
| [`common/eeper-base.yaml`](common/eeper-base.yaml) | shared package | WiFi + SNTP + TLS MQTT + the `eeper_publish` contract script |

To add a node type, include the base package and call `eeper_publish` with your
metric/value/unit/quality — the base handles the connection, the clock, and the wire
format so a new sensor can't get the bus plumbing wrong.

## The base package earns its keep

`common/eeper-base.yaml` encodes the things that are easy to get wrong on the hardened
bus and would otherwise silently break a node:

- **TLS on 8883 with a pinned CA** — the broker refuses plaintext and anonymous.
- **esp-idf framework** — required for MQTT-over-TLS with a custom CA (Arduino can't).
- **Home Assistant discovery OFF** — it publishes to `homeassistant/#`, which the
  per-device ACL forbids; a single denied publish disconnects the node.
- **Housekeeping under `eeper/dev/{id}/node/`** — birth/will live inside the device's
  ACL subtree but one level below the `eeper/dev/+/+` metric space, so eeper's ingestion
  ignores them instead of mis-parsing them as readings. MQTT log streaming is off.
- **SNTP clock, publish gated on it** — the contract `ts` is unix seconds and eeper
  derives online/offline from it, so `eeper_publish` stays silent until the clock is set.

See [`../PROVISIONING.md`](../PROVISIONING.md) for the full flash-and-pair walkthrough.
Fill `secrets.yaml` from `secrets.yaml.example` (git-ignored) and the per-node
substitutions from the eeper **Devices** screen.
