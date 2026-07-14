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

v1's audio nudge is **sustained sound level** — the robust, model-free behaviour of every classic audio baby monitor: in a quiet nursery, sustained sound above the ambient floor means the baby needs attention. **Cry classification** (telling a cry from a bark or a loud TV) ships **experimental and off by default**. This is a measured decision, not a shortcut: exhaustive evaluation (recorded in the M2.3 PR) showed pretrained YAMNet cannot carry cry classification to a first-class bar — at any false-nudge-safe operating point, sustained-episode recall for a single infant caps at ~0.70, because a cry the model cannot hear stays unheard for the whole episode (window errors correlate in time, so temporal voting cannot exceed the model's per-infant detectability). The trained model that unlocks first-class cry detection is **M2.6** (M2.5 de-risked it and found the wall is the corpus, not the model). The two signals follow the graceful-degradation principle applied to the roadmap: the sound-level nudge carries the product now, the classifier improves it later.

**Deliverables:** sound-level detector (per-window RMS/dBFS, adaptive quiet-only baseline, k-of-n sustained-elevation state machine → `sound_elevated` event; on by default for audio cameras, no model). Experimental cry classifier (pretrained YAMNet-class model via ONNX Runtime with eeper's versioned NumPy log-mel frontend, pet-suppressed window scoring + k-of-n episode detector → `cry_detected`; off by default) with confidence output and sensitivity settings. Model fetch tooling (`/models` manifest, checksum-verified download at first run). Long-form scene synthesis in the fixture tooling (multi-minute cry episodes + confuser-only nights, reused by M3.3's full-night traces).

**Testing criteria:**
- [AUTO] Model fetch: first run downloads the manifest's models, verifies checksums, and refuses a tampered file.
- [AUTO] Sound-level product gate on the frozen `fixtures-v1` eval split (M2.0), each threshold derived from a stated parent consequence and recorded next to the number: episode recall ≥ 0.90 on sustained cry episodes (a real crying spell must be caught); median onset→nudge latency ≤ 10 s (useful while the baby is still crying); ≤ 1 false event per synthesized 8 h quiet night (a quiet nursery must not manufacture wake-ups); a continuous white-noise-machine night is absorbed by the adaptive baseline. Encoded in CI, pinned to the fixture version.
- [AUTO] Cry-classifier window ratchet baselines on the same split (near-field + physically-based far-field recall/FPR) are recorded and RATCHETED: CI fails on a regression below the floor, but they do not block on an aspirational absolute — cry accuracy is M2.6's bar, not v1's. They are M2.6's starting line (M2.5 ratchets them up to the honestly-measured reality).
- [AUTO] Integration: a sustained sound streamed through the synthetic camera produces a `sound_elevated` event end-to-end (MQTT + `state_history`).
- [AUTO] ONNX Runtime CPU path runs on both amd64 and arm64 images (inference smoke test in multi-arch CI).
- [MANUAL] Bench: a recorded cry played from a speaker at realistic distance/volume in a quiet room raises a sound-level nudge; a quiet room over 30 minutes does not. (Real acoustics. A sound monitor honestly also nudges on other sustained sound — a loud TV, a barking dog — which is correct behaviour and documented; cry-vs-not discrimination is the M2.6 bench.)

## M2.4 — Events, clips, and nudges

**Deliverables:** event records linked to auto-promoted clips (pre/post roll), Tonight view v0 (event list with tappable clips), Web Push notifications with per-user preferences and quiet-hours toggle, nudge copy per the safety stance. Delivery is a **DB-as-queue**: the insight engine writes a nudge event with its delivery channels `pending`, and an api-side worker (Postgres `LISTEN/NOTIFY` for low latency + a reconciliation poll as the never-lost safety net) does the side effects and marks them — so a crash mid-delivery resumes losslessly. Delivery policy (quiet hours, per-user prefs, rate-limit) lives in that worker, never in the detector.

**Testing criteria:**
- [AUTO] Integration: an audio nudge (the primary `sound_elevated` event in v1; `cry_detected` too once M2.6 makes cry first-class) auto-promotes a clip spanning the configured pre/post roll; the event API returns the clip reference; the clip is playable.
- [AUTO] Playwright: the event appears in Tonight view without reload (WebSocket push) and its clip plays on tap.
- [AUTO] Web Push: a subscribed test client (headless push service) receives the nudge; users with notifications off or in quiet hours do not (matrix test).
- [AUTO] Copy lint: notification templates are checked against a denylist of clinical/alarm terms ("oxygen", "vital", "emergency", "apnea") — encoding the Section 2 stance as a test.
- [AUTO] Crash safety (the queue's whole point): the worker killed between event insert and delivery still sends the nudge exactly once on restart; an event whose `NOTIFY` is dropped is still delivered by the reconciliation poll within its window; a rolled-back event insert produces no side effects (the transactional `NOTIFY` never fires for it).
- [MANUAL] Push notifications arrive on physical iOS and Android with the PWA backgrounded and the phone locked. (OS push behavior is not reliably emulatable.)

## M2.5 — Cry ratchet + honest reframe (first-class cry is corpus-gated)

M2.3 promoted this as "train a model to unlock first-class cry." Before committing, a reproducible de-risk (recorded in the M2.5 PR) tested exactly that — eeper's frozen frontend + YAMNet features, a trained head (logistic + MLP, over both the 521-class AudioSet scores and the 1024-d embeddings, balanced + near/far augmented) over the **full donateacry corpus** (457 clips), split device-disjoint on donateacry's per-upload UUID (a reasonable but imperfect infant proxy). The finding, one level up from M2.3's: **on the corpus that exists, a trained head does not clear the bar and does not even beat the pretrained hand-tuned scorer** at any false-nudge-safe operating point (best trained near-field recall ~0.47 vs ~0.84 pretrained on the same split; any residual infant leakage would only flatter the trained head, which still lost). The binding confuser is cry-vs-animal, where the hand-tuned animal-band suppression is a strong inductive bias a naive head can't recover on held-out infants. **The ceiling is the corpus, not the model:** donateacry is the only cry source (457 clips, 84% one cry reason, ~7 s, near-field phone recordings, only a per-device UUID → no *guaranteed* infant-disjoint split), FSD50K supplies **confusers only** (there are no "FSD50K cry positives" in the corpus — an error in the original M2.5 premise), and there is no real far-field audio (far-field is synthesised). The pretrained model is already near this ceiling — ~0.80 near-field / ~0.76 far-field window recall and ~0.85 episode recall (aggregate on the fixture episode mix; M2.3's ~0.70 was the harder single-infant ceiling) at a false-nudge-safe point (the earlier far-field "collapse" was largely a shared-threshold calibration artifact, ~0.59 → ~0.76 at a far-optimal threshold).

So M2.5 is reframed honestly — the M2.3 pattern: measure the truth, ratchet, name the gap, give it a milestone. Cry stays experimental + **off by default** (the corpus can't support a first-class, on-by-default cry nudge); the on-by-default flip + blocking episode gate move to M2.6.

**Deliverables:** ratchet the cry window baselines UP to the honestly-measured numbers (CI fails on regression, the floor only moves up); record the de-risk method + numbers + the corrected corpus premise in the gate module and this plan.

**Testing criteria:**
- [AUTO] Cry window ratchets raised to the measured near/far recall + FPR on the frozen `fixtures-v1` eval split (near recall floor 0.78 / FPR ≤ 0.08, far recall floor 0.72 / FPR ≤ 0.11), CI failing on any regression below the new floor; thresholds pinned to the fixture version.
- [AUTO] The sound-level product gate (the primary v1 nudge) stays green, unchanged.
- [AUTO] ONNX Runtime CPU inference smoke on amd64 + arm64 (unchanged).

**Phase 2 exit:** all green through M2.4; end-to-end demo criterion — from speaker-played cry to phone nudge (**sound-level** in v1) with playable clip — passes on the bench ([MANUAL], recorded in checklist). Cry classification stays the experimental, ratcheted signal until M2.6 lands the corpus.

## M2.6 — Cry corpus expansion (the first-class-cry prerequisite)

The real unlock for first-class cry is a corpus that doesn't yet exist, not a training trick (proved by M2.5's de-risk). This milestone builds it, then re-runs the trained-model attempt against it.

**Deliverables:** a genuinely larger + more diverse cry corpus with **guaranteed infant-level identifiers** (for leakage-safe, infant-disjoint splits — donateacry gives only a per-device UUID proxy), the **FSD50K cry-labelled positives** the current corpus lacks, a **real room-impulse-response** corpus for honest far-field (convolution, not just synthetic reverb), and a carved **train split**. Then a reproducible training pipeline producing a checksum-pinned ONNX artifact (fetched + verified like the pretrained model); on a pass, the classifier flips to on-by-default and the episode gate becomes blocking.

**Testing criteria:**
- [AUTO] Reproducible training: the artifact rebuilds from the corpus manifest + a pinned pipeline on a clean machine (checksum match), fetched + checksum-verified at first run.
- [AUTO] Leakage-safe evaluation: the eval split is infant-disjoint from train (asserted, not just clip-hash-disjoint).
- [AUTO] Quality gate ratcheted UP past M2.5's floors: near-field cry recall ≥ 0.9 / FPR ≤ 0.1 AND far-field recall/FPR clearing a product-derived floor (on real-RIR far-field); episode recall ≥ 0.95 for sustained episodes at ≤ 1 false cry-nudge per synthesized 8 h night. Thresholds encoded in CI, pinned to the fixture version.
- [AUTO] ONNX Runtime CPU path on amd64 + arm64.
- [MANUAL] Bench: a speaker-played cry raises a *cry* nudge; 30 minutes of household TV / pet audio does not (real cry-vs-not discrimination — the capability the sound-level nudge intentionally does not claim).

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

# Phase 6 — Thermal Input (post-v1)

## M6.1 — Thermal capture node & publisher

**Deliverables:** MLX90640 driver integration on the Pi capture node (I²C, 2–4 Hz, default bus speed); publisher service emitting the §4.5 grid + derived-features messages under the standard sensor contract with mandatory `quality` (frame-read errors, checksum failures, and stale frames degrade quality rather than publishing bad grids); pairing via the existing M3.1 device flow (dynsec client + per-device role, no special-casing); device-health integration; synthetic thermal traces added to the sensor fleet harness.

**Testing criteria:**

- [AUTO] Contract validation: published grid and features messages validate against the §4.5 schema; malformed-frame injection (truncated grid, NaN temps) is dropped with quality degradation, never republished.
- [AUTO] Pairing parity: a thermal node pairs, publishes, unpairs, and revokes through the exact M3.1 flow; the M3.1 ACL matrix and reconcile tests pass with a thermal device class present.
- [AUTO] Failure handling: simulated I²C read failures (harness) flip device quality/health within the heartbeat window without crashing the publisher; recovery is automatic.
- [AUTO] Rate discipline: publisher never exceeds 4 Hz grid rate regardless of sensor refresh configuration.
- [MANUAL] Bench: physical MLX90640 on the Pi 4 node streams grids for 24 h without I²C lockup; a person-analog entering/leaving the 55° FOV at crib distance visibly changes the grid.

## M6.2 — Characterization & go/no-go gate

**Deliverables:** a recorded characterization corpus from the bench (person-analog and, where available, consented real-world nights): warm-body present/absent, blanket-covered vs uncovered, room-temperature sweeps, IR-illuminator and electronics heat as confounders; hand-labeled presence ground truth; derived-feature extractor (presence, warm-region features) tuned on a dev split; a written characterization report in `docs/` stating measured presence accuracy, confounder behavior, and an explicit recommendation.

**Testing criteria:**

- [AUTO] Presence quality gate on the frozen eval split: presence accuracy ≥ 0.95 with blanket-covered cases included, false-presence rate ≤ 0.05 against confounders (warm electronics, recently vacated bedding, IR illuminator heating). Derivation: presence is thermal's entire fusion contribution; below these numbers it adds noise to a signal camera+radar already provide.
- [AUTO] Eval/dev split discipline and fixture versioning per the M2.0 pattern (`thermal-fixtures-v1`, frozen, re-baselining recorded in PROGRESS.md).
- [MANUAL] Characterization report reviewed and go/no-go decision recorded.

**Gate:** if the [AUTO] gate cannot be met after tuning, Phase 6 stops here by design: the node remains a supported experimental input (M6.1 stands), the report documents why, and M6.3 is not built. This is a valid, successful outcome — the milestone's product is the decision, not the integration.

## M6.3 — Fusion integration & UI (conditional on M6.2 go)

**Deliverables:** thermal presence/features registered as a fusion extractor under the graceful-degradation registry; corroboration rules updated (thermal presence as a corroborating signal for sleep/wake and occupancy, never a sole trigger for distress); Tonight/Devices UI showing thermal presence state and device health — no temperature readouts anywhere in user-facing UI; replay traces extended with thermal channels.

**Testing criteria:**

- [AUTO] Replay suite: full-night replays with thermal channels maintain or improve sleep/wake epoch agreement vs the recorded non-thermal baseline (ratchet — thermal must not degrade fusion), across all input-subset combinations including thermal-only-plus-audio and thermal-absent.
- [AUTO] Degradation: unpairing the thermal node mid-replay produces valid states with no crash and correct extractor de-registration.
- [AUTO] Safety assertions: copy lint passes on all thermal UI strings; Playwright asserts no UI surface renders grid temperatures or any °C value from the thermal input; the features-only fusion boundary is enforced by schema (fusion layer has no code path consuming raw grids).
- [AUTO] Session integrity and corroboration tests from M3.3 re-run green with thermal present.
- [MANUAL] Bench overnight with thermal fused: timeline reviewed against reality notes; blanket-occlusion periods spot-checked for presence stability.

**Phase 6 exit:** either M6.3 complete with the fusion ratchet green, or M6.2's documented no-go — both close the phase.

---

# Phase 7 — Sleep Timelapse (post-v1)

An opt-in, per-camera timelapse of a night's sleep (§7.3): stills captured at a configurable interval and assembled into a video with a burned-in wall-clock time overlay, optional motion-adaptive capture density, and a sleep movement map. Awareness only — the movement map is relative activity, never a medical/diagnostic readout (§2). Local-only, off by default, retention-governed, and independent of the fusion pipeline (it consumes the movement signal read-only; it never feeds fusion).

## M7.1 — Timelapse capture & assembly (fixed interval)

**Deliverables:** a per-camera timelapse capture service that grabs a still at a configurable interval and stores frames (each stamped with its true capture time) in a dedicated, retention-governed timelapse store — separate from the recording ring buffer and promoted clips; an assembler that stitches the stills into an MP4 at a configurable output frame rate with a **burned-in wall-clock time overlay** driven by each frame's capture-time stamp; a timelapse session model (per-camera, opt-in, start/stop) + API to configure the interval, start/stop, and list/download; admin-gated per the role model. No motion adaptation yet.

**Testing criteria:**

- [AUTO] Capture cadence: over a simulated clock, stills are captured at the configured interval within tolerance; the assembled video's frame count equals the captured-frame count and its duration equals frames ÷ output-fps.
- [AUTO] Time-stamp fidelity: every captured still carries its true capture time; the assembler applies the overlay from those stamps, so frame N is stamped with capture-time N — verified on a synthetic sequence with known times (the pixel-legibility of the burned-in text is the [MANUAL] item).
- [AUTO] Isolation + retention: timelapse artifacts live in their own store bounded by a quota/age policy (the M4.3 pattern) and never evict, read, or write the recording ring buffer or promoted clips.
- [AUTO] Opt-in + role gating: timelapse capture is off by default and per-camera opt-in; configuration is admin-only (a viewer is denied per the grandparent-mode role model).
- [MANUAL] Bench: an overnight fixed-interval timelapse assembles into a watchable MP4 with a correct, legible time overlay.

## M7.2 — Motion-adaptive capture & sleep movement map

**Deliverables:** an **optional** motion-adaptive cadence — the capturer consumes the existing movement signal (the M2.2 camera-motion score / M3.3 fused activity, read-only) and shortens the interval within a configured `[min, max]` band during movement, lengthening it during stillness; a per-frame **sleep movement map** derived from the same signal, aligned 1:1 to the timelapse frames and stored alongside the timelapse; a clean fall-back to the fixed interval + a flat map when no movement signal is available. The movement map ships even where adaptive capture is disabled (it is derived post-hoc from the recorded movement).

**Testing criteria:**

- [AUTO] Adaptive cadence: replaying a known movement trace, capture density rises during movement windows and falls during quiet ones, always within `[min_interval, max_interval]` and never exceeding M7.1's max rate; deterministic under replay.
- [AUTO] Movement-map alignment: every timelapse frame maps to one movement value on the same timeline; the map length equals the frame count and the series matches the fusion activity for that window within tolerance.
- [AUTO] Graceful degradation: with no movement signal available, capture falls back to the fixed interval and the map is flat/empty — no crash, a valid timelapse is still produced.
- [MANUAL] Bench: adaptive capture visibly densifies around real movement, and the movement map lines up with the activity seen in the video.

## M7.3 — Timelapse UI (configure, playback, map + time)

**Deliverables:** a **Timelapse** view — per-camera enable + interval + motion-adaptive toggle (with `[min, max]`) configuration; a list of captured timelapses; a player that shows the burned-in time overlay and renders the sleep movement map as a graph **synced to playback** (a moving playhead over the map / a scrubbable strip); download; wired into the nav and the role model (config admin-only per grandparent mode).

**Testing criteria:**

- [AUTO] Config round-trip: interval + adaptive settings persist and reload; the config surface is admin-gated (a viewer is denied).
- [AUTO] Playback + map: Playwright asserts a timelapse plays, the movement-map graph renders, and the time/playhead indicator tracks the playback position (map synced to the video timeline).
- [AUTO] Safety + roles: copy lint passes on all timelapse UI strings (awareness framing, no medical/diagnostic claims); the role sweep confirms the timelapse config surface follows the grandparent-mode gating.
- [MANUAL] Usability: an overnight timelapse is reviewed end-to-end — the time overlay is legible, the movement map matches the video, and the controls are intuitive.

**Phase 7 exit:** M7.1–M7.3 complete (fixed interval → adaptive + map → UI), **or** a documented scope decision: because motion-adaptive capture is explicitly optional, shipping M7.1 + M7.3 with a fixed interval and a post-hoc movement map (deferring M7.2's adaptive cadence) is a valid reduced-scope outcome, recorded in PROGRESS.md.

---

## Ongoing (post-milestone) automation

- The full-night replay job (M3.3) and the bench performance gate (M5.2) run nightly on main.
- The model quality gates (M2.3, M3.3) run on any change to models, preprocessing, or fusion logic.
- The [MANUAL] checklist is re-executed in full for every minor release; individual items are re-run when their subsystem changes.
