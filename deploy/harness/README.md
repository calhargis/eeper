# harness — test-only synthetic inputs

Synthetic inputs that let CI (and local dev) exercise the pipeline without
hardware. **Not part of the production stack** (excluded from the image build).

- `synthetic-camera/` — [mediamtx](https://github.com/bluenviron/mediamtx) serving
  an H.264 test pattern (a moving timecode) plus a known 1 kHz sine audio track on
  `rtsp://<host>:8554/cam`. Contract-conformant: H.264 baseline, 720p, 15 fps.
- `sensor-fleet/` — a Python MQTT publisher replaying scripted mmWave/PIR/pulse-ox
  traces on the `eeper/{node}/…` sensor contract (`{ts, type, value, unit, quality}`
  and `{ts, hr, spo2, perfusion, quality}`). Timing, dropout, and malformed output
  are env-controllable for later fuzzing/resilience tests.
- `mosquitto/` — a plaintext, anonymous **test** broker. The hardened production
  broker (TLS, per-device credentials, ACLs) lands in M3.1.

## Run

```bash
docker compose -f deploy/harness/docker-compose.yml up -d --build
pytest deploy/harness/tests -v   # self-test: camera streams + fleet publishes
docker compose -f deploy/harness/docker-compose.yml down -v
```

The **`harness`** CI workflow runs this self-test on every PR (intended to be a
required check for later milestones). The **browser harness** (Playwright) lives
in `web/e2e/` and runs in the `stack` workflow's `e2e` job against a fresh core
stack.
