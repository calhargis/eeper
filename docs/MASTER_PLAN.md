# Open Source Baby Monitoring System — Master Plan

**Project name:** eeper
**Status:** Draft v1.0 — July 2026
**License target:** AGPL-3.0 (server) / MIT (client libraries) — TBD

---

## 1. Vision & Goals

An open source, self-hosted baby monitoring system that runs on hardware people already own. It ingests any combination of four input types — video, audio, pulse oximetry, and motion — and turns them into live monitoring plus derived insights: sleep/wake state, movement level, calm/distressed state, and long-term sleep history and trends.

The system is **LAN-first**: it runs on a server inside the home and is monitored from devices inside the home, with secure remote access as an opt-in layer and a future path to an optionally hosted (cloud) deployment.

### Design principles

1. **Universal compatibility.** The server targets *protocols and formats, not devices*. Any camera that can produce an RTSP/H.264 stream, any microphone that can produce a PCM/Opus stream, and any sensor that can publish MQTT is a supported input. Hardware-specific code is confined to small, optional "adapter" components at the edge.
2. **Graceful degradation.** Every feature works with whatever inputs are present. One camera alone is a complete, useful system; each additional input enriches the insight layer rather than being required by it.
3. **Speed.** Live video must feel instant (sub-second glass-to-glass). Insight events (cry detected, motion spike) must surface within ~2 seconds. The UI must be responsive on a mid-range phone.
4. **Security by default.** This is a camera pointed at a child inside a home. There is no acceptable "we'll add auth later." TLS, authentication, and least-privilege are on from the first commit, and the default deployment exposes nothing to the internet.
5. **Insights, not medical alarms.** See Section 2.

### Non-goals (v1)

- Cloud-hosted multi-tenant service (planned for later; architecture accommodates it — see Section 12).
- Native iOS/Android apps (the PWA covers this; native apps are a later optimization).
- Medical-grade vital sign monitoring or any diagnostic claims.
- Smart-home actuation (lights, sound machines). The event bus makes this easy for integrators, but it is out of scope for the core.

---

## 2. Safety & Regulatory Stance

This stance shapes product copy, UI design, and alerting behavior, so it is defined before the architecture.

In September 2025 the U.S. FDA issued a safety communication warning against over-the-counter infant monitors that claim to measure vital signs (heart rate, SpO2, respiration, temperature) without marketing authorization, on the grounds that inaccurate readings can drive both false alarm and false reassurance. Independent clinical testing of consumer pulse-ox baby monitors has documented exactly those failure modes. The hobbyist sensors this project supports (e.g., MAX3010x optical modules) are consumer-grade parts whose accuracy degrades precisely in the ranges that would matter clinically.

Therefore:

1. The system is positioned as a **sleep-insight and awareness tool**, never as a medical device, apnea monitor, or SIDS-prevention product. This language appears in the README, the UI onboarding, and the docs.
2. Pulse oximetry is an **optional input, insights-only**. Its data feeds trends and the fusion model (e.g., heart-rate variability as a sleep-state feature). The UI displays it as trend context, not as a red-line vital sign readout.
3. Notifications are worded as **nudges to check** ("High movement and crying detected — you may want to look in"), never as clinical alerts ("LOW OXYGEN").
4. No feature may claim to detect, predict, or prevent any medical condition. Contribution guidelines encode this so the boundary survives community PRs.
5. Onboarding includes a plain-language disclaimer and a link to safe-sleep guidance, and requires acknowledgment before pulse-ox input can be enabled.
6. **Environmental temperature is not a vital sign.** Where the system reports a temperature, it reports the _environment_ — nursery air and crib/bedding **surface** — as sleep-climate awareness, gated behind the same acknowledgment as pulse-ox. It never assigns a temperature to the infant, never presents a thermal reading as body temperature, and never judges an infant against an "ideal" or "optimal" body temperature. Non-contact IR arrays measure surface radiance at a distance, not core temperature; the boundary is a safety line and a physical fact at once.

This stance is also an engineering simplification: it removes hard-realtime and fail-safe requirements from the pulse-ox path, which would otherwise be impossible to honor on hobbyist hardware.

---

## 3. System Architecture

The system is a set of containerized services communicating over well-defined internal interfaces. The core rule: **clients talk only to the API gateway; the server talks only to normalized streams and topics, never to hardware.**

```
┌─────────────────────────  EDGE (optional adapters)  ─────────────────────────┐
│  Pi CSI camera ──rpicam shim──┐        USB webcam ──ffmpeg shim──┐           │
│  IP camera (native RTSP) ─────┤        I2S/USB mic ──ffmpeg──────┤           │
│  ESP32 + mmWave/PIR ──ESPHome──► MQTT  ESP32 + MAX3010x ─────────► MQTT      │
└───────────────┬──────────────────────────────┬───────────────────────────────┘
                │ RTSP/H.264 (+AAC/Opus audio) │ MQTT (TLS, authenticated)
┌───────────────▼──────────────────────────────▼───────────────────────────────┐
│                              SERVER (Docker Compose)                          │
│                                                                               │
│  media-gateway (go2rtc)     mosquitto (MQTT broker)                           │
│      │ WebRTC/RTSP/HLS          │                                             │
│      ▼                          ▼                                             │
│  insight-engine  ◄──frames/audio/sensor topics──►  publishes insight events   │
│      │                                                                        │
│      ▼                                                                        │
│  timescaledb (sensor + insight time series)   recorder (clips, ring buffer)  │
│      ▲                                              ▲                         │
│      └──────────────  api (FastAPI)  ───────────────┘                         │
│                          │  REST + WebSocket + WebRTC signaling               │
│  caddy (reverse proxy, TLS, auth gate)                                        │
└──────────────────────────┬────────────────────────────────────────────────────┘
                           │ HTTPS/WSS (LAN)          │ WireGuard/Tailscale
                     ┌─────▼─────┐              ┌─────▼─────┐
                     │  PWA in   │              │  Remote   │
                     │  browser  │              │  phone    │
                     └───────────┘              └───────────┘
```

### Services

| Service | Technology | Responsibility |
|---|---|---|
| `media-gateway` | go2rtc | Ingest any RTSP/RTMP/USB source; republish as WebRTC (live view), RTSP (internal consumers), HLS (recordings fallback) |
| `mosquitto` | Eclipse Mosquitto | Authenticated, TLS-secured MQTT bus for all non-video sensor data and internal events |
| `insight-engine` | Python (PyTorch/ONNX Runtime) | Subscribes to frames, audio, and sensor topics; runs heuristics + pretrained models; publishes insight events; writes time series |
| `recorder` | Python + ffmpeg | Continuous ring buffer; persists event-triggered clips; retention management |
| `timescaledb` | PostgreSQL + TimescaleDB | Time-series data (sensor readings, insight states) plus relational app data (users, devices, sessions) in one engine |
| `api` | FastAPI | REST + WebSocket API, WebRTC signaling relay, auth, business logic |
| `web` | SvelteKit PWA (static build) | Live view, dashboard, history, settings; installable on phones |
| `caddy` | Caddy | Single entry point: TLS termination, HTTP→HTTPS, security headers, forward-auth |

Every service is optional except `api`, `caddy`, and `timescaledb`. Compose profiles (Section 11) let a user run video-only, sensors-only, or the full stack.

### Why one database

TimescaleDB is chosen over InfluxDB because it keeps relational data (users, devices, sleep sessions) and time-series data (readings, states) in a single PostgreSQL engine — one backup story, one query language, and continuous aggregates for the trends UI. This reduces operational surface area, which matters for self-hosters.

---

## 4. Input Pipelines

Each input type has a **normalization contract**: the format the server consumes, regardless of source hardware. Adapters exist to convert non-conforming hardware into the contract and run at the edge (or on the server host if the device is plugged into it).

### 4.1 Video (Phase 1 — the first input)

**Contract:** RTSP carrying H.264 (baseline/main profile, ≤1080p, ≤15 fps is plenty) with optional AAC/Opus audio track. H.264 is mandated as the universal baseline: hardware decode exists on effectively every client and server, and browsers play it over WebRTC without transcoding. H.265/AV1 may be accepted later as opt-in for storage efficiency, transcoded at the gateway when a client can't play them.

**Supported sources, in order of "just works":**
1. Any ONVIF/RTSP IP camera (native conformance, zero adapter).
2. USB UVC webcam via a bundled ffmpeg adapter container (V4L2 → RTSP).
3. Raspberry Pi CSI camera (incl. Camera Module 3 NoIR for night vision) via a bundled rpicam adapter container.
4. Anything else that can push RTMP/RTSP (old phones running an IP-camera app are a deliberately supported "hardware people already own" path).

**Flow:** source → go2rtc → (a) WebRTC to clients for sub-second live view, (b) internal RTSP to insight-engine and recorder. go2rtc handles protocol fan-out without re-encoding, keeping CPU near zero for pass-through.

**Recording:** the recorder keeps a configurable ring buffer (default 24 h) of segmented H.264 on disk and promotes segments to permanent, indexed clips when the insight-engine flags an event (cry, high motion, state change). Full-night retention is configurable for users who want to scrub through an entire night.

### 4.2 Audio (Phase 2)

**Contract:** 16 kHz mono PCM (or Opus) — either as the audio track of the video stream or as a standalone RTSP/MQTT-published stream from a mic node (ESP32 + I2S MEMS mic via ESPHome, or USB mic via ffmpeg adapter).

Audio is consumed by the insight-engine for cry/distress detection and sound-level tracking, and passed through to clients for listening. Two-way talk-back is a post-v1 candidate.

### 4.3 Motion & presence (Phase 3)

**Contract:** MQTT messages on `eeper/{node}/motion` (and related topics) in a small JSON schema: `{ts, type, value, unit, quality}`.

Sources: 60 GHz mmWave radar nodes (presence + respiration-rate estimation, contactless — the recommended motion sensor), PIR nodes, under-mattress accelerometer/load-cell nodes, all via ESPHome or MicroPython on ESP32. Additionally, the insight-engine derives **camera-based motion** (frame differencing / optical flow) from the video pipeline, so "motion" as an insight input exists even with zero dedicated motion hardware — an instance of the graceful-degradation principle.

### 4.4 Pulse oximetry (Phase 4, optional, insights-only)

**Contract:** MQTT on `eeper/{node}/pulseox`: `{ts, hr, spo2, perfusion, quality}`. The `quality` field is mandatory — the insight-engine discards low-confidence samples rather than storing misleading data.

Reference node: ESP32 + MAX30102/MAX30101 publishing at 1 Hz aggregated readings. Per Section 2, this data feeds trends and fusion features only; it never drives alarms, and the UI presents it as historical context with an accuracy caveat.

### 4.5 Thermal (post-v1, optional)

**Contract:** MQTT on `eeper/{node}/thermal`: `{ts, grid: [32×24 temps, °C, row-major], t_min, t_max, t_mean, quality}` at 2–4 Hz, plus a derived low-rate features message `{ts, presence, presence_confidence, warm_region_area, warm_region_centroid}`. The full grid is published for characterization and debugging; the fusion layer consumes only the derived features.

Reference node: MLX90640 (55° FOV) on I²C, hosted by a Pi capture node (see docs/reference-builds/pi4-all-in-one.md). Per §2, thermal readings are surface temperatures and feed presence/trend features only — never temperature-as-vital-sign, never displayed as a body-temperature readout. Thermal's value hypothesis (presence detection robust to blanket occlusion and lighting, complementary to camera motion and radar) is validated by an explicit characterization gate before fusion integration.

**Environmental temperature + live view (Phase 8).** Beyond presence, the node derives two _environmental_ temperatures from the grid — a **nursery-ambient** proxy (a stable cool-background region) and a **crib/bedding surface** temperature — always with the occupant (warm) region excluded, published low-rate on `eeper/{node}/thermal_environment`: `{ts, ambient_c, crib_surface_c, quality}`, and stored as a trend series. No occupant/body temperature is ever derived, stored, or shown. A live **thermal view** relays the grid to the client as a **relative false-color heatmap** — awareness, not a thermometer: the occupant renders as _presence_, never as a temperature, and any numeric °C on screen is the occupant-excluded environmental readout. This sharpens the §2/§4.5 boundary rather than loosening it: a relative heatmap and environmental °C are permitted under the pulse-ox acknowledgment flow; a **body-temperature readout of the baby, and any "optimal infant temperature" judgment, remain prohibited** — the schema and UI tests enforce that no occupant temperature value exists in the pipeline. Specified as Phase 8 (§13); it builds on M6.1 and is independent of the M6.2/M6.3 fusion track.

---

## 5. Insight Engine

The intelligence layer for v1 is **heuristics plus pretrained models** — no custom model training is required to ship, but the architecture leaves a clean seat for trained models later.

### 5.1 Design

A modular pipeline: each available input feeds one or more **feature extractors**; a **fusion layer** combines whatever features exist into the three core state outputs. Extractors register themselves based on which inputs are live, so the same fusion code serves every hardware combination.

```
video ──► frame sampler (1–5 fps) ──► motion score (frame differencing)
                                  └─► pose/presence (pretrained person/pose model)
audio ──► 1 s windows ──► sound level (RMS)
                      └─► cry classifier (pretrained audio-event model, YAMNet-class)
mmWave ─► presence, movement index, respiration estimate
pulseox ► HR, HRV features (quality-gated)
                        │
                        ▼
              fusion layer (rules v1)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   sleep/wake     movement level   calm/distressed
```

### 5.2 v1 logic

- **Movement level** (low/medium/high): windowed motion score from camera and/or radar, hysteresis-smoothed to avoid flapping.
- **Sleep/wake:** rule-based state machine over movement + sound + presence (e.g., sustained low movement + low sound + presence → asleep; sustained movement or crying → awake), with minimum-dwell times. This mirrors actigraphy-style classification and is robust with even one input.
- **Calm/distressed:** driven primarily by the cry classifier confidence plus movement; distress requires corroboration across ≥2 signals when ≥2 inputs exist (reduces false positives), falls back to single-signal thresholds otherwise.
- **Sleep sessions:** the state machine emits session boundaries (fell asleep / woke), which are stored as first-class records powering the history and trends UI (total sleep, wake count, longest stretch, week-over-week charts via TimescaleDB continuous aggregates).

### 5.3 Model runtime

Pretrained models run via **ONNX Runtime** on CPU by default — chosen because it is the most universally deployable runtime (x86/ARM, no vendor lock). Optional hardware acceleration is a swappable execution provider: Hailo (Pi AI HAT), CUDA/TensorRT, or OpenVINO, enabled by a Compose profile. Model files are versioned artifacts downloaded at first run, not baked into images.

### 5.4 Later (post-v1) seat for trained models

The event store doubles as a labeled-data substrate: users can correct states in the UI ("she was actually awake here"), producing training labels for a future personalized sequence model (e.g., a small temporal CNN/transformer over fused features). This is explicitly out of v1 scope but the data schema records everything needed for it.

A second post-v1 model seat is the **sleep-climate** correlation (Phase 8, §7.4): learning, from a household's own accumulated nights, the nursery-temperature band associated with _that_ baby's best-observed sleep. It is an environmental insight computed over the trend store (nursery temperature vs. the M4.1 sleep metrics) — never a physiological target, never a claim about the infant's body.

---

## 6. Data Model & Storage

Single PostgreSQL/TimescaleDB instance, three logical areas:

**Relational (app data):** `users`, `devices` (registered input nodes and their contracts), `cameras`, `sleep_sessions`, `events` (insight events with type, confidence, clip reference), `settings`, `api_tokens`.

**Time series (hypertables):** `sensor_readings` (device_id, ts, metric, value, quality), `state_history` (ts, state_type, value, confidence, contributing_inputs), and the optional-input series (`pulseox_readings`; `thermal_features` — presence; and, for Phase 8, `thermal_environment` — nursery-ambient + crib-surface temperatures, occupant-excluded). Continuous aggregates precompute hourly/nightly rollups so trend queries stay fast on a Pi.

**Blob storage (filesystem volume):** video segments and event clips under `/data/media`, indexed by the `events` table, with a retention daemon enforcing configurable disk quotas (oldest-first eviction, event clips outlive raw ring-buffer segments).

Backup story: one `pg_dump` plus one media directory — deliberately simple for self-hosters.

---

## 7. API & Frontend

### 7.1 API (FastAPI)

- **REST** for CRUD and queries: `/api/v1/cameras`, `/devices`, `/sessions`, `/events`, `/trends`, `/settings`, `/auth`.
- **WebSocket** `/api/v1/live` pushing state changes and insight events to clients in real time.
- **WebRTC signaling** proxied to go2rtc so clients never talk to an internal service directly.
- OpenAPI schema published and versioned from day one (`/api/v1/...`) — this is the seam that keeps LAN and future cloud deployments compatible (Section 12) and lets third parties (e.g., Home Assistant) integrate cleanly.

### 7.2 Frontend (SvelteKit PWA)

Svelte chosen for small bundle size and speed on low-end phones. Installable PWA with push-capable service worker (Web Push for nudges when the app is backgrounded).

Views: **Live** (WebRTC video, current states, sound level), **Tonight** (timeline of the current/most recent night: states, events, tappable clips), **Trends** (sleep duration, wake counts, patterns over weeks — and, post-v1, the nursery sleep-climate: temperature vs. sleep, §7.4), **Devices** (add/manage inputs, connection health), **Settings** (users, notifications, retention, remote access, disclaimers, theme), and (post-v1) **Timelapse** (§7.3) and **Thermal** (§7.4).

Latency budget: WebRTC glass-to-glass < 500 ms on LAN; state event to UI < 2 s; cold page load < 3 s on a mid-range phone over LAN.

### 7.3 Sleep Timelapse (post-v1)

A per-camera, **opt-in** timelapse of a night's sleep. The recorder captures a still at a configurable interval and assembles them into a downloadable video with a **burned-in wall-clock time overlay**, so the hour is legible as the night plays back. Capture can **optionally** densify with motion: when the movement signal (the M2.2 camera-motion score / M3.3 fused activity) shows the baby moving, the interval shortens within a configured `[min, max]` band and lengthens again during stillness — more frames where more happens. From that same movement signal each frame carries an activity value, forming a **sleep movement map** — a relative-activity graph aligned 1:1 to the timelapse and scrubbable alongside playback.

Awareness only: the movement map is relative activity, **never a medical or diagnostic readout** (§2). Timelapse imagery is stored **locally**, off by default, and governed by the retention daemon under its own quota/age policy — it never touches the recording ring buffer or promoted clips. Specified as Phase 7 (§13).

### 7.4 Thermal environment & sleep climate (post-v1)

An **opt-in** surface for the thermal node (§4.5) that turns it from a pure presence sensor into a **sleep-climate** instrument — strictly _environmental_, never physiological (§2):

- **Live thermal view** — the grid relayed to the client as a **relative false-color heatmap** for awareness (like the WebRTC Live view, but thermal). The occupant renders as _presence_; it is never labeled with a temperature. Live-only, not stored.
- **Environmental readouts** — the current **nursery-ambient** and **crib/bedding surface** temperatures (occupant-excluded, §4.5), shown as environmental context with an accuracy caveat, behind the pulse-ox-style acknowledgment.
- **Sleep-climate trends** — the nursery temperature over each night, charted alongside the sleep session, with a plain-language _relative_ note: "tonight ran warmer than your typical night." Comparisons are always to the household's **own** baseline, never to a prescribed target.
- **Learned sweet-spot** (the ML seat, §5.4) — once enough nights accrue, the nursery-temperature band associated with _this_ baby's best-observed sleep (longest stretch / fewest wakes / fastest settle): "your baby has tended to sleep longest around 19 °C." An observation about the **environment**, learned from the family's own data.

Hard boundaries, enforced by copy lint + Playwright + schema: **no body-temperature readout of the infant, no "ideal/optimal infant temperature," no fever or illness inference.** Any reference to published safe-sleep _room_ ranges is general, clearly-labeled environmental information — not personalized advice. Opt-in, disclaimer-gated, retention-governed; independent of the M6.2/M6.3 fusion track. Specified as Phase 8 (§13).

---

## 8. Security Architecture

Threat model: an in-home camera feed of a child is a high-value target; typical risks are exposed ports, default credentials, unencrypted LAN traffic, and supply-chain issues. Defaults are locked down; convenience is opt-in.

1. **No internet exposure by default.** The Compose stack binds to the LAN interface only. Remote access is exclusively via WireGuard/Tailscale (documented first-class), never port forwarding; the docs actively warn against exposing the stack.
2. **TLS everywhere, including LAN.** Caddy terminates HTTPS with a locally provisioned CA (generated at install; instructions for trusting it on devices). WebRTC media is DTLS-SRTP encrypted by protocol. MQTT runs TLS with per-device credentials.
3. **Authentication:** username/password + optional TOTP 2FA; short-lived JWT access tokens with rotating refresh tokens; per-device MQTT credentials with topic-scoped ACLs (a compromised sensor node can publish only its own topics); scoped API tokens for integrations.
4. **Authorization roles:** admin / viewer (grandparent mode: live view only, no settings, no history export).
5. **Container hardening:** distroless/slim images, non-root users, read-only root filesystems, no privileged containers, internal Docker network with only Caddy exposing ports, pinned image digests, Renovate + image scanning in CI, signed releases.
6. **Secrets:** generated at first run into a `.env`/Docker secrets file; no default passwords anywhere; first-boot wizard forces admin credential creation.
7. **Privacy posture:** all inference is local; no telemetry; no cloud dependency for any v1 feature. An optional "pause monitoring" control stops capture and is visibly indicated.

---

## 9. Performance & Optimization

- **Zero-transcode media path:** go2rtc repackages H.264 rather than re-encoding; CPU cost of live viewing is near zero, preserving headroom for inference.
- **Inference decimation:** models run on sampled frames (1–5 fps) and 1 s audio windows — sleep states change over seconds/minutes, not frames. This keeps the full stack comfortably within a Pi 5 or any dual-core x86 box.
- **Backpressure by design:** insight-engine drops frames rather than queueing when behind (freshness beats completeness for state estimation).
- **DB efficiency:** hypertable compression on data older than 7 days; continuous aggregates for all trend queries; the Trends UI never scans raw readings.
- **Benchmark gates in CI:** a reference "Pi 5 (4 GB) + 1080p camera + mic" profile must sustain < 60 % steady-state CPU and meet the latency budget before release.

---

## 10. Repository & Quality

Monorepo: `/server` (Python services), `/web` (SvelteKit), `/adapters` (ffmpeg/rpicam shims), `/firmware` (ESPHome configs + MicroPython for sensor nodes), `/deploy` (Compose files, install script), `/docs`, `/models` (model manifest + fetch tooling).

Quality: type-checked Python (mypy) and TS; unit tests plus an integration test that boots the Compose stack with a synthetic RTSP source and asserts end-to-end event flow; recorded sample nights (with consent, synthetic where possible) as regression fixtures for the insight-engine; conventional commits; CI builds multi-arch images (amd64 + arm64).

---

## 11. Packaging & Deployment

**Distribution: Docker Compose, run anywhere.** One `docker compose up` on any amd64/arm64 Linux box (Pi 4/5, NAS, old laptop, mini-PC, VM). An `install.sh` handles prerequisites, secret generation, and the first-boot wizard URL.

**Compose profiles** map to hardware reality:
- `core` — api, caddy, db, web (always on)
- `video` — media-gateway, recorder, insight-engine vision extractors
- `audio` — audio extractors (piggybacks media-gateway)
- `sensors` — mosquitto + sensor ingestion
- `pulseox` — pulse-ox ingestion (requires explicit enable + acknowledged disclaimer)
- `accel-hailo` / `accel-cuda` — optional inference acceleration

Adapters (USB cam, Pi CSI cam) ship as standalone Compose snippets that can run on the server host or on a separate edge Pi pointing at the server — the same normalization contract either way.

---

## 12. LAN-First, Cloud-Ready

The architecture keeps three seams that make an eventual hosted offering an extension, not a rewrite:

1. **Clients speak only the versioned API + WebRTC.** A hosted deployment serves the same API from a different origin; the PWA is already origin-agnostic.
2. **All media flows through the gateway.** Adding a TURN relay and (later) cloud-side gateway instances extends the same topology across NAT.
3. **Single-tenant assumptions are quarantined.** The schema carries a `household_id` from day one (constant in self-hosted mode), so multi-tenancy is a policy layer, not a migration.

Remote-access roadmap: v1 ships WireGuard/Tailscale docs (household-only, near-zero attack surface) → v1.x adds an optional self-hosted TURN for WebRTC through strict NATs → v2 evaluates an opt-in hosted relay/instance service with end-to-end encryption so the host never sees media.

---

## 13. Phased Roadmap

**Phase 0 — Skeleton (2–3 wks):** Compose scaffold, Caddy+TLS, DB, auth, first-boot wizard, CI, multi-arch images. *Exit: secure empty app installable anywhere.*

**Phase 1 — Video (3–4 wks):** go2rtc integration, RTSP contract + USB/CSI adapters, WebRTC live view in PWA, ring-buffer recorder. *Exit: sub-second live viewing of any camera, from any browser in the house.*

**Phase 2 — Audio + first insights (3–4 wks):** audio pipeline, sound level, pretrained cry classifier, camera motion score, movement-level state, event clips, Web Push nudges. *Exit: "crying detected" nudge with a tappable clip.*

**Phase 3 — Sensors + sleep states (3–4 wks):** MQTT bus + ESPHome reference nodes (mmWave, PIR), fusion state machine, sleep/wake + calm/distressed, Tonight timeline. *Exit: full night tracked as a session with states and events.*

**Phase 4 — Trends + pulse-ox (2–3 wks):** continuous aggregates, Trends UI, optional pulse-ox ingestion behind disclaimer, retention daemon, viewer role. *Exit: v1.0 feature-complete.*

**Phase 5 — Hardening & release (2 wks):** security review, benchmark gates, docs (including safety copy review), sample-hardware guide, public release. *Exit: v1.0 released.*

**Post-v1 phases (optional, individually feature-gated):**

- **Phase 6 — Thermal input:** MLX90640 low-res presence, characterized behind an explicit go/no-go gate before any fusion integration (§4.5).
- **Phase 7 — Sleep Timelapse:** opt-in per-camera timelapse with a wall-clock time overlay, optional motion-adaptive capture, and a sleep movement map (§7.3).
- **Phase 8 — Thermal environment & sleep climate:** opt-in environmental temperature capture (nursery-ambient + crib-surface, occupant-excluded), a relative false-color live heatmap, temperature-vs-sleep trends with own-baseline anomaly notes, and a learned nursery sleep-temperature sweet-spot. Environmental only, never a body-temperature readout or an infant-temperature target (§2, §7.4). Builds on the M6.1 node; independent of the M6.2/M6.3 fusion gate.

---

## 14. Risks & Open Questions

- **Cry-classifier false positives** (pets, TV, sibling): mitigated by corroboration rules and per-household sensitivity tuning; personalized models are the long-term fix.
- **WebRTC through strict NATs** for remote users: VPN sidesteps it in v1; TURN is the v1.x answer.
- **Pi availability/pricing volatility** (2026 RAM-driven price spikes): mitigated by the run-anywhere posture — the Pi is a reference target, not a requirement.
- **Community scope creep toward medical claims:** mitigated by contribution guidelines and PR templates encoding Section 2.
- **Open:** trademark check on "eeper"; AGPL vs Apache-2.0 final call; whether ONVIF PTZ control makes v1; minimum supported browser set for WebRTC; post-v1 candidate inputs: access-point mode for networkless/travel use (idea credited to OpenBabyMonitor; see prior-art.md). Thermal input, formerly a candidate here, is now specified — see §4.5 and Phase 6.

