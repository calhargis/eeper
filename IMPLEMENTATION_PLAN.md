# Implementation Plan — Open Source Baby Monitoring System

**Companion to:** MASTER_PLAN.md (v1.0)
**Status:** Draft v1.0 — July 2026

This document breaks the six phases of the master plan into concrete milestones. Each milestone lists its deliverables and its **testing criteria** — the checks that must pass for the milestone to be considered done. Every criterion is labeled:

- **[AUTO]** — verifiable by an automated test that runs in CI (unit, integration, or end-to-end).
- **[MANUAL]** — requires a human (physical hardware, perceptual quality, real-network conditions). Manual tests have a written procedure in `/docs/testing/` and their results are recorded in the milestone's release checklist.

The bias is automated-first: a criterion is only [MANUAL] when the thing being verified is inherently physical or perceptual.

**Quality-gate thresholds must trace to a stated product consequence, with the derivation recorded next to the number.** A gate that asserts "recall ≥ 0.9" without an answer to "0.9 of *what*, and who suffers at 0.85?" cannot be reasoned about when it's breached — you can't tell whether it matters. This is a hard lesson from M2.3, where an untraceable window-level "recall ≥ 0.9 / FPR ≤ 0.1" bar (inherited from a first draft, provable against no user harm) masked the real question until the gate was rebuilt around episodes and nights — the quantities a parent actually experiences. Where a model can't yet meet a product-derived bar, record the honest measured number as a **ratcheted baseline** (CI fails on regression, the floor only moves up) and give the gap a milestone rather than a euphemism.

---

## Testing Infrastructure (built in M0.3, used everywhere)

The plan depends on a small set of test harnesses, defined here once:

1. **Synthetic camera** — a containerized ffmpeg process that serves a looped test video (with embedded timestamps burned into frames and a known audio track) over RTSP. Lets CI exercise the full media path with no hardware.
2. **Synthetic sensor fleet** — a Python MQTT publisher that replays recorded/scripted sensor traces (mmWave, PIR, pulse-ox) with controllable timing, dropout, and malformed messages.
3. **Fixture library** — versioned audio clips (infant cries, ambient noise, TV, pet sounds), video segments (still sleep, rolling, sitting up), and full-night sensor traces with hand-labeled ground truth. Sourced from public datasets (e.g., donated cry corpora) and synthetic generation; anything recorded from real families requires documented consent and is stored out-of-repo with a fetch manifest.
4. **Stack harness** — pytest fixtures that bring up the full Compose stack (with synthetic inputs) in CI, plus teardown and log capture.
5. **Browser harness** — Playwright driving the PWA headlessly, including WebRTC playback checks via `getStats()`.
6. **Reference-hardware bench** — one physical Pi 5 (4 GB) + Camera Module 3 NoIR + USB mic + ESP32 nodes, used for the recurring [MANUAL] suite and the performance gate. A self-hosted CI runner on this bench automates what it can (perf sampling) even though the setup itself is physical.

---

# Phase 0 — Skeleton

## M0.1 — Repository & CI foundation

**Deliverables:** monorepo layout (`/server`, `/web`, `/adapters`, `/firmware`, `/deploy`, `/docs`, `/models`), linting/type-checking config, conventional-commit enforcement, CI pipeline building multi-arch (amd64/arm64) images, pinned base image digests.

**Testing criteria:**
- [AUTO] CI runs lint + mypy + TS type-check on every PR and fails on violations.
- [AUTO] CI produces amd64 and arm64 images for every service and pushes to the registry on merge to main.
- [AUTO] Image scan step (Trivy or equivalent) fails the build on critical CVEs in release images.
- [AUTO] A commit with a malformed message is rejected by CI.

## M0.2 — Compose scaffold, TLS, and first-boot security

**Deliverables:** `docker-compose.yml` with `core` profile (caddy, api, timescaledb, web placeholder), `install.sh` (prereq check, secret generation, local CA provisioning), first-boot wizard forcing admin credential creation, LAN-only port binding.

**Testing criteria:**
- [AUTO] Integration test: `install.sh` on a clean container host produces a running stack with zero default credentials present in any config, env file, or database row.
- [AUTO] All HTTP requests to port 80 are redirected to HTTPS; TLS certificate chains to the locally generated CA; HSTS and baseline security headers present (asserted by integration test).
- [AUTO] Port scan assertion: only Caddy's ports are reachable from outside the Docker network; direct connections to api/db containers from the host network fail.
- [AUTO] API endpoints (other than first-boot/auth) return 401 before wizard completion and after logout.
- [AUTO] Containers run as non-root with read-only root filesystems (asserted by inspecting the running stack in CI).
- [MANUAL] Trust the local CA on one iOS and one Android device following the docs; PWA loads without certificate warnings. (Physical devices; doc usability is part of the check.)

## M0.3 — Auth, users, and test harnesses

**Deliverables:** JWT access/refresh flow, optional TOTP, admin/viewer roles, `users` and `api_tokens` schema and endpoints; synthetic camera, synthetic sensor fleet, stack harness, browser harness (items 1, 2, 4, 5 above).

**Testing criteria:**
- [AUTO] Unit + integration: login, refresh rotation, logout revocation, TOTP enrollment and challenge, expired-token rejection, viewer-role denial on admin endpoints (full matrix).
- [AUTO] Brute-force lockout: N failed logins trigger backoff (integration test with clock control).
- [AUTO] Harness self-test: CI job boots the stack with the synthetic camera and sensor fleet and asserts both are reachable/publishing — this job becomes a required check for all later milestones.

**Phase 0 exit:** all criteria above green; a stranger can `install.sh` on any Linux box and reach a secure, empty, login-gated app.

---

# Phase 1 — Video

## M1.1 — Media gateway and the RTSP contract

**Deliverables:** go2rtc service wired into Compose (`video` profile), camera registration API (`/cameras`), stream config validation against the contract (H.264, ≤1080p), internal RTSP re-serve for downstream consumers.

**Testing criteria:**
- [AUTO] Integration: registering the synthetic camera makes its stream available via go2rtc's WebRTC and internal RTSP endpoints within 5 s.
- [AUTO] Contract validation: registering a stream with an unsupported codec (test source in H.265) is rejected with an actionable error message.
- [AUTO] Resilience: killing and restarting the synthetic camera results in automatic stream recovery within 15 s, with camera health status transitioning offline → online via the API.
- [AUTO] Signaling path: WebRTC negotiation completes exclusively through the api service's signaling relay; direct client access to go2rtc's port from outside the Docker network fails.

## M1.2 — Live view in the PWA

**Deliverables:** SvelteKit PWA shell (installable, service worker), Live view with WebRTC playback, camera health indicators, multi-camera switching.

**Testing criteria:**
- [AUTO] Playwright: authenticated user reaches Live view and WebRTC playback starts; `getStats()` shows flowing video frames within 3 s of page load.
- [AUTO] Playwright: unauthenticated access to Live view redirects to login; viewer role can access Live view.
- [AUTO] Latency gate (bench runner): burned-in timestamp comparison between synthetic source and rendered frame shows glass-to-glass < 500 ms on LAN.
- [AUTO] Lighthouse CI: PWA installability checks pass; performance budget met on throttled mobile profile.
- [MANUAL] Install the PWA on physical iOS and Android phones; verify live view starts, survives screen lock/unlock, and switching between two cameras works. (Real-device WebRTC and lifecycle behavior can't be fully trusted in emulation.)
- [MANUAL] Night-vision quality check: Camera Module 3 NoIR + IR illuminator in a dark room produces a usable image at the default bitrate. (Perceptual.)

## M1.3 — Adapters (USB and Pi CSI cameras)

**Deliverables:** ffmpeg USB adapter container, rpicam CSI adapter container, docs for pointing an old phone's IP-camera app at the gateway.

**Testing criteria:**
- [AUTO] The USB adapter, fed a V4L2 loopback device with test video in CI, produces a contract-conformant RTSP stream consumable end-to-end (gateway → browser harness).
- [AUTO] Adapter images build multi-arch and pass the same contract-validation suite as native cameras.
- [MANUAL] Bench: a physical USB webcam and a CSI Camera Module 3 each register and stream through their adapters on the reference Pi.
- [MANUAL] An Android phone running a common RTSP-camera app registers and streams following only the written doc.

## M1.4 — Recorder (ring buffer + clips)

**Deliverables:** segmented ring-buffer recording, configurable retention, clip-promotion API (internal), playback endpoints, retention daemon with disk quota.

**Testing criteria:**
- [AUTO] Integration: after N minutes of synthetic streaming, segments exist on the media volume, oldest segments are evicted when the configured quota is exceeded, and promoted clips survive eviction.
- [AUTO] Clip promotion through the internal API yields a playable H.264 file whose duration and timestamps match the request (probed with ffprobe in CI).
- [AUTO] Playback endpoint enforces auth and streams a clip playable in the browser harness.
- [AUTO] Crash safety: killing the recorder mid-segment and restarting loses at most the active segment; the index and prior segments remain consistent.

**Phase 1 exit:** all green; the reference bench sustains 24 h of continuous recording + live viewing under the CPU budget (< 60 % steady-state, sampled by the bench runner — [AUTO] on the self-hosted runner).

---

# Phase 2 — Audio & First Insights

## M2.0 — Labeled audio fixture library

Long-lead item; blocks the M2.3 quality gate and feeds M3.3's full-night traces. Can start any time after M0.1 (needs only CI, not the stack).

**Deliverables:** `/fixtures` tooling — a per-clip manifest (source URL, license, sha256, labels, verification status) and a deterministic fetch-and-build script; **no third-party audio committed to the repo**. Source set: donateacry-corpus (ODbL) and per-clip CC0/CC-BY FSD50K clips for cry positives; MUSAN, LibriSpeech/Common Voice, and FSD50K household/pet clips for confusers; project-recorded hard negatives (white-noise machine, lullaby tracks, TV-with-crying-baby, sibling vocalizations). NC-licensed sources (e.g., ESC-50) are excluded by policy. Scene synthesis via Scaper: room impulse responses, nursery noise floor, swept SNRs, seeded and deterministic, emitting frame-accurate onset/offset annotations. Two-person verification pass over all source clips. Frozen, versioned eval split (≥ 100 cry scenes, ≥ 300 confuser scenes) plus a disjoint dev split for threshold tuning. License/provenance file including the ODbL share-alike note for any future redistributed subset.

**Testing criteria:**
- [AUTO] Manifest integrity: every entry has a source URL, a license identifier from the allowed set (no NC), a sha256, labels, and a verification status; CI fails on any missing field or disallowed license.
- [AUTO] Reproducible build: two independent CI runs of `fixtures build` from a clean cache produce bit-identical audio and annotation files (hash comparison).
- [AUTO] Fetch verification: a tampered or substituted source file (wrong sha256) fails the build.
- [AUTO] Split discipline: eval and dev splits are disjoint at the source-clip level (no source clip contributes scenes to both); asserted by the build.
- [AUTO] Statistical floor: eval split contains ≥ 100 cry scenes and ≥ 300 confuser scenes, with every confuser category (speech, music/TV, pets, white noise/lullaby, sibling/other-child) represented by ≥ 30 scenes.
- [AUTO] Annotation sanity: every synthesized scene's onset/offset annotations fall within the clip bounds, and every cry scene contains ≥ 1 cry event annotation.
- [MANUAL] Verification pass: two reviewers have listened to all source clips against the rubric (cry present: yes/no/unclear; unclear discarded); disagreements resolved and recorded in the manifest.
- [MANUAL] Realism spot-check: a reviewer listens to a random 20-scene sample of synthesized eval audio and confirms it plausibly resembles far-field nursery capture (no synthesis artifacts, sane levels).

**Done means:** `fixtures build` produces the versioned library (`fixtures-v1`) from the manifest alone on a clean machine, and the eval split is frozen — any subsequent change bumps the fixture version and re-baselines dependent gates (recorded in PROGRESS.md).

## M2.1 — Audio pipeline

**Deliverables:** audio extraction from camera streams and standalone mic sources (USB adapter; ESP32/I2S deferred to M3.1's MQTT bus for control, audio via RTSP), normalization to 16 kHz mono, listen-in audio in Live view.

**Testing criteria:**
- [AUTO] Integration: the synthetic camera's known audio track arrives at the insight-engine as 16 kHz mono PCM windows with correct sample values (checksum against fixture).
- [AUTO] Browser harness: Live view plays audio; `getStats()` shows flowing audio packets.
- [MANUAL] Bench: real microphone audio is intelligible in the PWA with no gross sync drift against video over 10 minutes. (Perceptual.)

## M2.2 — Insight engine core + motion score

**Deliverables:** insight-engine service skeleton (frame sampler, feature-extractor registry, MQTT event publishing, state write path to TimescaleDB), camera motion score via frame differencing, movement-level state (low/medium/high) with hysteresis, `state_history` and `events` schema live.

**Testing criteria:**
- [AUTO] Unit: motion score on fixture clips ranks still < rolling < sitting-up (ordering assertion, not absolute values).
- [AUTO] Unit: hysteresis — a synthetic motion trace oscillating around a threshold produces ≤ 1 state change (no flapping).
- [AUTO] Integration: streaming the "rolling" fixture produces a movement-level state change event on MQTT and a row in `state_history` within 2 s of the motion onset (latency budget assertion).
- [AUTO] Backpressure: when the engine is artificially slowed, frames are dropped rather than queued (memory stays bounded; freshness of last processed frame stays < 3 s).
- [AUTO] Graceful degradation: engine starts and runs with video-only input; extractor registry reports exactly the extractors matching available inputs.

## M2.3 — Audio nudges: sound level + experimental cry detection

v1's audio nudge is **sustained sound level** — the robust, model-free behaviour of every classic audio baby monitor: in a quiet nursery, sustained sound above the ambient floor means the baby needs attention. **Cry classification** (telling a cry from a bark or a loud TV) ships **experimental and off by default**. This is a measured decision, not a shortcut: exhaustive evaluation (recorded in the M2.3 PR) showed pretrained YAMNet cannot carry cry classification to a first-class bar — at any false-nudge-safe operating point, sustained-episode recall for a single infant caps at ~0.70, because a cry the model cannot hear stays unheard for the whole episode (window errors correlate in time, so temporal voting cannot exceed the model's per-infant detectability). The trained model that unlocks first-class cry detection is **M2.5**. The two signals follow the graceful-degradation principle applied to the roadmap: the sound-level nudge carries the product now, the classifier improves it later.

**Deliverables:** sound-level detector (per-window RMS/dBFS, adaptive quiet-only baseline, k-of-n sustained-elevation state machine → `sound_elevated` event; on by default for audio cameras, no model). Experimental cry classifier (pretrained YAMNet-class model via ONNX Runtime with eeper's versioned NumPy log-mel frontend, pet-suppressed window scoring + k-of-n episode detector → `cry_detected`; off by default) with confidence output and sensitivity settings. Model fetch tooling (`/models` manifest, checksum-verified download at first run). Long-form scene synthesis in the fixture tooling (multi-minute cry episodes + confuser-only nights, reused by M3.3's full-night traces).

**Testing criteria:**
- [AUTO] Model fetch: first run downloads the manifest's models, verifies checksums, and refuses a tampered file.
- [AUTO] Sound-level product gate on the frozen `fixtures-v1` eval split (M2.0), each threshold derived from a stated parent consequence and recorded next to the number: episode recall ≥ 0.90 on sustained cry episodes (a real crying spell must be caught); median onset→nudge latency ≤ 10 s (useful while the baby is still crying); ≤ 1 false event per synthesized 8 h quiet night (a quiet nursery must not manufacture wake-ups); a continuous white-noise-machine night is absorbed by the adaptive baseline. Encoded in CI, pinned to the fixture version.
- [AUTO] Cry-classifier window ratchet baselines on the same split (near-field + physically-based far-field recall/FPR) are recorded and RATCHETED: CI fails on a regression below the floor, but they do not block on an aspirational absolute — cry accuracy is M2.5's bar, not v1's. They are M2.5's starting line.
- [AUTO] Integration: a sustained sound streamed through the synthetic camera produces a `sound_elevated` event end-to-end (MQTT + `state_history`).
- [AUTO] ONNX Runtime CPU path runs on both amd64 and arm64 images (inference smoke test in multi-arch CI).
- [MANUAL] Bench: a recorded cry played from a speaker at realistic distance/volume in a quiet room raises a sound-level nudge; a quiet room over 30 minutes does not. (Real acoustics. A sound monitor honestly also nudges on other sustained sound — a loud TV, a barking dog — which is correct behaviour and documented; cry-vs-not discrimination is the M2.5 bench.)

## M2.4 — Events, clips, and nudges

**Deliverables:** event records linked to auto-promoted clips (pre/post roll), Tonight view v0 (event list with tappable clips), Web Push notifications with per-user preferences and quiet-hours toggle, nudge copy per the safety stance. Delivery is a **DB-as-queue**: the insight engine writes a nudge event with its delivery channels `pending`, and an api-side worker (Postgres `LISTEN/NOTIFY` for low latency + a reconciliation poll as the never-lost safety net) does the side effects and marks them — so a crash mid-delivery resumes losslessly. Delivery policy (quiet hours, per-user prefs, rate-limit) lives in that worker, never in the detector.

**Testing criteria:**
- [AUTO] Integration: an audio nudge (the primary `sound_elevated` event in v1; `cry_detected` too once M2.5 makes cry first-class) auto-promotes a clip spanning the configured pre/post roll; the event API returns the clip reference; the clip is playable.
- [AUTO] Playwright: the event appears in Tonight view without reload (WebSocket push) and its clip plays on tap.
- [AUTO] Web Push: a subscribed test client (headless push service) receives the nudge; users with notifications off or in quiet hours do not (matrix test).
- [AUTO] Copy lint: notification templates are checked against a denylist of clinical/alarm terms ("oxygen", "vital", "emergency", "apnea") — encoding the Section 2 stance as a test.
- [AUTO] Crash safety (the queue's whole point): the worker killed between event insert and delivery still sends the nudge exactly once on restart; an event whose `NOTIFY` is dropped is still delivered by the reconciliation poll within its window; a rolled-back event insert produces no side effects (the transactional `NOTIFY` never fires for it).
- [MANUAL] Push notifications arrive on physical iOS and Android with the PWA backgrounded and the phone locked. (OS push behavior is not reliably emulatable.)

## M2.5 — Trained cry model (first-class cry detection)

Promoted from the M2.3 finding: pretrained YAMNet cannot distinguish a cry from other sustained sounds to a first-class bar. This milestone builds a reproducible **trained** cry model — a head on YAMNet embeddings or a small purpose-built CNN — on a sourced cry corpus (donateacry full + FSD50K cry positives + near/far-field augmentation), unlocking cry classification as a first-class, on-by-default nudge and lifting far-field (room-corner camera) placement and latency. Its starting line is the M2.3 window ratchet baselines; each gate below must ratchet those UP. Sits after M2.4 because the sound-level nudge already carries the Phase 2 demo — this is a capability upgrade, not a blocker.

**Deliverables:** cry-corpus manifest + a reproducible training pipeline producing a checksum-pinned ONNX artifact (fetched + verified like the pretrained model); the classifier flips to on-by-default; the episode gate becomes blocking.

**Testing criteria:**
- [AUTO] Reproducible training: the model artifact rebuilds from the corpus manifest + a pinned pipeline on a clean machine (checksum match), and is fetched + checksum-verified at first run.
- [AUTO] Quality gate on the frozen fixtures eval split, ratcheted UP from M2.3's baselines: near-field cry recall ≥ 0.9 / FPR ≤ 0.1 AND far-field recall/FPR clearing a product-derived floor; episode recall ≥ 0.95 for sustained episodes at ≤ 1 false cry-nudge per synthesized 8 h night. Thresholds encoded in CI, pinned to the fixture version.
- [AUTO] ONNX Runtime CPU path runs on both amd64 and arm64 (inference smoke test in multi-arch CI).
- [MANUAL] Bench: a speaker-played cry raises a *cry* nudge; 30 minutes of household TV / pet audio does not (real cry-vs-not discrimination — the capability the sound-level nudge intentionally does not claim).

**Phase 2 exit:** all green through M2.4; end-to-end demo criterion — from speaker-played cry to phone nudge (sound-level in v1) with playable clip — passes on the bench ([MANUAL], recorded in checklist). M2.5 upgrades the nudge from sound-level to cry classification and can land after the demo.

---

# Phase 3 — Sensors & Sleep States

## M3.1 — MQTT bus and device onboarding

**Deliverables:** mosquitto with TLS + per-device credentials + topic-scoped ACLs, device registration/pairing flow in the UI, JSON schema validation for the sensor contract, device health (last-seen, quality stats).

**Testing criteria:**
- [AUTO] ACL matrix: device A's credentials cannot publish to device B's topics or subscribe to internal event topics (asserted for every ACL class).
- [AUTO] Malformed input fuzzing: schema-violating and oversized messages from the synthetic fleet are rejected, logged, and do not crash or slow ingestion (fuzz corpus in CI).
- [AUTO] Integration: a synthetic mmWave node pairs via the API flow, publishes a trace, and its readings land in `sensor_readings` with correct quality fields.
- [AUTO] Health: stopping a synthetic node flips its device status to offline within the heartbeat window; UI reflects it (Playwright).
- [AUTO] TLS enforcement: plaintext MQTT connection attempts are refused.

## M3.2 — Reference sensor firmware

**Deliverables:** ESPHome configs for mmWave (presence, movement index, respiration estimate) and PIR nodes; MicroPython fallback template; provisioning doc.

**Testing criteria:**
- [AUTO] ESPHome configs compile in CI for target boards; emitted topic structure and payloads validate against the sensor contract schema (config-level unit tests + replayed compile-time schema checks).
- [MANUAL] Bench: physical mmWave node detects presence/absence and movement of a person-analog (moving heat/motion target) in a crib-distance setup; PIR node detects gross motion; both survive 24 h without disconnect.
- [MANUAL] Provisioning doc walkthrough: a team member who didn't write the firmware flashes and pairs a node using only the doc.

## M3.3 — Fusion state machine (sleep/wake, calm/distressed)

**Deliverables:** fusion layer consuming all registered extractors, sleep/wake state machine with dwell times, calm/distressed with multi-signal corroboration, sleep-session records (fell asleep / woke boundaries), Tonight timeline v1 (states + events on one scrubbable track).

**Testing criteria:**
- [AUTO] Replay suite: labeled full-night fixture traces (video-derived features + sensor traces) replayed through the fusion layer must achieve ≥ 90 % epoch agreement with hand labels for sleep/wake and detect every labeled wake ≥ 3 min (thresholds in CI as a quality gate).
- [AUTO] Combinatorial degradation: the same replay suite runs under input subsets (video-only, radar-only, video+audio, all) and must produce valid states in every combination, with accuracy allowed to degrade but never crash or emit undefined states.
- [AUTO] Corroboration rule: with ≥ 2 inputs live, a distress state requires ≥ 2 corroborating signals (unit test on crafted single-signal traces asserting no distress emitted).
- [AUTO] Session integrity: replayed nights produce exactly the labeled number of sessions with boundary timestamps within ±2 min of labels; sessions survive an engine restart mid-night (crash-recovery test).
- [AUTO] Playwright: Tonight timeline renders states and events for a replayed night; scrubbing to an event plays its clip.
- [MANUAL] Live overnight run on the bench (person-analog or consenting household): the resulting timeline is reviewed against reality notes for gross errors. (Ground truth of a real night is inherently manual.)

**Phase 3 exit:** all green; a full replayed night flows from raw inputs to an accurate Tonight timeline with zero manual intervention ([AUTO], and this becomes the permanent end-to-end regression job).

---

# Phase 4 — Trends & Pulse-Ox

## M4.1 — Trends

**Deliverables:** TimescaleDB continuous aggregates (hourly/nightly rollups), compression policy (> 7 days), Trends UI (sleep duration, wake counts, longest stretch, week-over-week), CSV export.

**Testing criteria:**
- [AUTO] Aggregate correctness: rollups over a seeded month of session data match independently computed values exactly.
- [AUTO] Query performance gate: every Trends API query over a seeded year of data returns < 200 ms on the bench runner.
- [AUTO] Compression: data older than 7 days is compressed; queries spanning compressed and uncompressed ranges return identical results to an uncompressed control.
- [AUTO] Playwright: Trends renders charts for seeded data; CSV export matches the API data; viewer role cannot export.

## M4.2 — Pulse-ox ingestion (optional, gated)

**Deliverables:** `pulseox` Compose profile, enable flow requiring acknowledged disclaimer, ESP32 + MAX3010x reference firmware with mandatory quality field, quality-gated ingestion, trend-context UI presentation (no red-line vitals, accuracy caveat shown), HR/HRV features exposed to fusion.

**Testing criteria:**
- [AUTO] Gating: pulse-ox endpoints and UI are absent/inert unless the profile is enabled AND the disclaimer is acknowledged by an admin (state matrix test).
- [AUTO] Quality gate: synthetic traces with low `quality` are discarded (not stored, not fused); discard rate is observable in device health.
- [AUTO] Copy lint extended: pulse-ox UI strings pass the clinical-terms denylist; the accuracy caveat string is asserted present on every pulse-ox view (Playwright).
- [AUTO] Fusion: HR features from a synthetic trace are consumed by the fusion layer only when quality-gated samples exist (degradation test).
- [MANUAL] Bench: reference sensor on an adult wearer produces plausible, quality-flagged readings; motion artifacts are visibly down-weighted. (Physical optical sensing; adult wearer only — no infant testing by the project.)

## M4.3 — Operations polish

**Deliverables:** retention daemon final (quota + age policies across media and time series), backup/restore doc and script (`pg_dump` + media), viewer "grandparent mode" polish, settings surface complete.

**Testing criteria:**
- [AUTO] Backup/restore round-trip in CI: seeded stack → backup → fresh stack → restore → data and clips identical (checksummed).
- [AUTO] Retention matrix: combinations of quota/age policies evict exactly the expected artifacts.
- [AUTO] Playwright role sweep: viewer sees live + tonight only; every settings/export/device route denies.

**Phase 4 exit:** v1.0 feature-complete; full [AUTO] suite green; [MANUAL] checklist current.

---

# Phase 5 — Hardening & Release

## M5.1 — Security review

**Deliverables:** dependency audit, secrets scan, threat-model review against Section 8, external penetration test (or structured community security review) of a default install.

**Testing criteria:**
- [AUTO] CI: zero critical CVEs; secrets scanner clean; the port-exposure, TLS, auth-matrix, and ACL suites from earlier milestones all still green (they are the regression harness).
- [AUTO] Auth fuzzing: token tampering, replay, and downgrade attempts against the API all rejected (test corpus).
- [MANUAL] Pen test of a default install finds no critical/high findings, or all such findings are fixed and re-tested. (Adversarial creativity isn't automatable.)

## M5.2 — Performance gate & docs

**Deliverables:** reference-profile benchmark automation on the bench runner, install/hardware/safety docs, sample-hardware guide, contribution guidelines with the Section 2 safety boundary and PR template.

**Testing criteria:**
- [AUTO] Bench gate: reference profile (Pi 5 4 GB, 1080p camera, mic, 2 sensor nodes, full stack) sustains < 60 % steady-state CPU, meets all latency budgets, and runs 72 h without OOM, crash, or stream loss.
- [AUTO] Docs: link checker + install-doc smoke test (script extracts and executes the documented install commands on a clean VM).
- [MANUAL] Cold-start usability: two external testers install from docs alone on non-Pi hardware (a NAS or laptop) and reach a working live view; friction points filed as issues.
- [MANUAL] Safety copy review: a clinician or child-health-literate reviewer reads all user-facing safety/onboarding copy for accuracy and tone.

**Phase 5 exit / v1.0 release criteria:** every [AUTO] suite green on both architectures; every [MANUAL] procedure executed and recorded for the release candidate; signed images published; release notes include the safety stance verbatim.

---

## Ongoing (post-milestone) automation

- The full-night replay job (M3.3) and the bench performance gate (M5.2) run nightly on main.
- The model quality gates (M2.3, M3.3) run on any change to models, preprocessing, or fusion logic.
- The [MANUAL] checklist is re-executed in full for every minor release; individual items are re-run when their subsystem changes.
