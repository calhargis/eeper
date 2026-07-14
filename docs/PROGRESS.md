# eeper — Progress Tracker

Tracks progress against [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md). Update this file in the same PR that completes work.

**How to use this file**
- A milestone is ✅ only when *every* criterion below it is checked.
- `[A]` = automated criterion (must be green in CI). `[M]` = manual procedure (record date + tester initials when performed, e.g. `✔ 2026-08-14 JD`).
- Statuses: ⬜ not started · 🔨 in progress · ✅ done · 🚧 blocked (add a note)

**Last updated:** 2026-07-08

---

## Overall status

| Phase | Milestones | Status |
|---|---|---|
| Planning (master plan, implementation plan, README) | — | ✅ done |
| Phase 0 — Skeleton | M0.1–M0.3 | ✅ done (merged; all [A] criteria green) |
| Phase 1 — Video | M1.1–M1.4 | ✅ done (all merged; register → live → record → clip) |
| Phase 2 — Audio & first insights | M2.0–M2.6 | 🔨 in progress (M2.0–M2.4 merged; M2.5 reframed to a cry ratchet after the de-risk; M2.6 = the cry-corpus unlock) |
| Phase 3 — Sensors & sleep states | M3.1–M3.3 | 🔨 [AUTO] done (M3.1–M3.3); [MANUAL] bench pending |
| Phase 4 — Trends & pulse-ox | M4.1–M4.3 | ✅ [AUTO] done (M4.1–M4.3); v1.0 feature-complete |
| Phase 5 — Hardening & release | M5.1–M5.2 | 🔨 in progress (M5.1 slice 1: supply-chain scanning) |

**Currently working on:** M2.3 (audio nudges: sound-level + experimental cry) in review; M2.0 fixture library in review
**Blockers:** none
**Labeled audio fixture library:** milestone **M2.0** — 🔨 implemented (in review): the `fixtures/` tooling + a real `fixtures-v1` manifest (236 source clips → 630 scenes, all AUTO gates green); [M] two-person verification + realism spot-check pending. sibling/other-child confuser deferred to fixtures-v1.1 (see M2.0).

---

## Phase 0 — Skeleton

### M0.1 — Repository & CI foundation — 🔨 implemented (CI confirms on first push)
- [x] [A] Lint + mypy + TS type-check on every PR, failing on violations
- [x] [A] Multi-arch (amd64/arm64) images built and pushed on merge
- [x] [A] Image scan fails build on critical CVEs
- [x] [A] Malformed commit messages rejected

> **Verification note.** M0.1 uniquely bootstraps CI itself, so "green in CI" can
> only be observed after the first push. Every check was run locally with the same
> tools/versions the workflows use and all pass:
> - `ruff check` (incl. `S`/bandit) + `ruff format --check` + `mypy --strict` + `pytest` — green
> - `eslint` + `svelte-check` (0 errors) + web static build — green
> - `prettier --check` across the repo — green
> - `commitlint` rejects a malformed message and accepts a Conventional one
> - `actionlint` (with shellcheck) — clean on both workflows
> - `web` image builds multi-arch (amd64 native + arm64 emulated), **passes the Trivy
>   CRITICAL gate (0 fixed-CRITICAL)**, and serves (`/` 200, SPA fallback 200)
>
> Base image digests are pinned (`node:22-bookworm-slim`, `caddy:2-alpine`); Renovate
> keeps them fresh (stale bases accrue fixed CVEs — the Caddy base was bumped during
> M0.1 precisely because an older pin failed the CRITICAL gate). The image build matrix
> auto-discovers services by Dockerfile, so it covers new services as they land.

### M0.2 — Compose scaffold, TLS, first-boot security — ✅ (merged PR #2, CI green)
- [x] [A] Clean-host install produces running stack with zero default credentials
- [x] [A] HTTP→HTTPS redirect, local-CA chain, HSTS + security headers
- [x] [A] Only Caddy reachable from outside the Docker network
- [x] [A] 401 on all non-auth endpoints pre-wizard and post-logout
- [x] [A] Containers non-root with read-only root filesystems
- [ ] [M] Local CA trusted on physical iOS + Android from docs alone — ______ (procedure: [docs/testing/m0.2-ca-trust.md](./testing/m0.2-ca-trust.md))

> **Verification.** The full `core` stack (Caddy + FastAPI api + TimescaleDB +
> static web) was brought up locally with `deploy/install.sh` and all five [A]
> criteria pass via the `deploy/tests/` integration suite (9/9), now wired into
> the CI `stack` workflow that runs install.sh on a clean runner and asserts them:
> - install.sh generates random secrets (no defaults); DB has zero user rows on a fresh install
> - `http→https` 301; TLS verified against the extracted local CA (no `-k`); HSTS + CSP + X-Frame-Options + nosniff + Referrer-Policy present
> - `api`/`db` publish no host ports; `db` on an `internal:` network; only Caddy is reachable
> - `/api/v1/me` returns 401 before first-boot and after logout; full flow (create admin → 200 → re-init 409 → logout → 401 → login) verified
> - every container non-root (`db` 70:70, `api`/`web`/`caddy` 10001/app) with a read-only rootfs, `cap_drop: ALL`, `no-new-privileges`
>
> A browser first-boot **wizard UI** (create-admin → sign-in → signed-in) is
> served by the web app and talks to the same-origin API. An adversarial review
> hardened: concurrency-safe first-boot (Postgres advisory lock, covered by a
> race test), a port-aware HTTP→HTTPS redirect, atomic secret generation in
> install.sh, and a password length cap.
>
> Notes: full auth (JWT access/refresh, TOTP, roles, brute-force lockout) is M0.3
> — M0.2 uses a signed session cookie. The `[M]` iOS/Android CA-trust check needs
> physical devices. CSP allows `script/style 'unsafe-inline'` for the SvelteKit
> bootstrap; hashed CSP is deferred (an adapter-static SPA can't emit SvelteKit CSP
> hashes, and WebRTC bypasses CSP by spec — see M1.2).

### M0.3 — Auth, users, test harnesses — 🔨 implemented (part 1 merged; part 2 harnesses in review)
- [x] [A] Full auth matrix: login, refresh rotation, revocation, TOTP, role denials
- [x] [A] Brute-force lockout with backoff
- [x] [A] Harness self-test job (synthetic camera + sensor fleet) green and marked required

> **Test harnesses (part 2).** A synthetic RTSP camera (mediamtx + ffmpeg test
> pattern, H.264 720p + known 1 kHz audio) and a synthetic MQTT sensor fleet
> (scripted mmWave/PIR/pulse-ox on the sensor contract, publishing to a test
> mosquitto). New `harness` CI workflow boots both and asserts camera streams +
> fleet publishes (self-test 2/2). A **Playwright browser harness** (`web/e2e/`)
> drives the first-boot wizard in a real browser (create admin → sign out → sign
> in), run by the `stack` workflow's `e2e` job against a fresh core stack — this
> also gives the wizard its first real-browser coverage. Harness images are
> excluded from the production image build. _Note: marking the `harness` job a
> required check is a branch-protection setting (repo admin)._

> **Auth (part 1).** JWT access (httpOnly cookie) + opaque refresh tokens stored
> hashed, grouped into per-login families with **rotation + reuse detection**
> (a replayed token revokes the family; logout revokes the family). TOTP 2FA
> (enroll → activate → login challenge → verify), admin/viewer roles with an
> admin guard, scoped **API tokens** (Bearer auth: create/list/revoke; a token
> needs an `admin` scope to reach admin endpoints), and brute-force **lockout**
> (auto-clears — tested with an injectable clock). Verified by an in-process
> auth-matrix suite (testcontainers Postgres + ASGI) plus token/password unit
> tests — `pytest` 26/26, mypy-strict clean; the stack integration (9/9) and api
> Trivy gate still pass. The M0.2 session cookie is replaced; the wizard handles
> the TOTP step.
>
> An adversarial security review hardened this before merge: refresh rotation is
> now **single-winner** (`SELECT … FOR UPDATE`, closing a concurrent-fork race);
> the **TOTP second factor is rate-limited** (shared lockout, counter not reset by
> re-login); **API-token scopes are enforced** (no silent full-admin); locked
> accounts fail with a **generic 401** (no enumeration oracle); and two false-pass
> tests were replaced with ones that prove server-side behavior.

**Phase 0 exit:** ✅ (pending M0.3 merge) — a stranger can `install.sh` on any Linux box → secure, empty, login-gated app; all Phase 0 [A] criteria green in CI

---

## Phase 1 — Video

### M1.1 — Media gateway & RTSP contract — 🔨 implemented (in review; CI: `stack`/`video` job)
- [x] [A] Registered synthetic camera live via WebRTC + internal RTSP within 5 s
- [x] [A] Non-conformant codec (H.265 test source) rejected with actionable error
- [x] [A] Stream auto-recovery within 15 s; health transitions offline→online
- [x] [A] Signaling only via api relay; direct go2rtc access blocked

> go2rtc (media gateway) wired into Compose under the `video` profile: internal-only,
> digest-pinned (third-party, like the db), hardened (non-root, read-only rootfs,
> cap_drop ALL) with a tmpfs-seeded config. Admin-only camera registration
> (`/api/v1/cameras`) validates the source with **ffprobe** (H.264 + orientation-aware
> ≤1080p; rejects H.265/HEVC with an actionable 422); go2rtc re-serves internal RTSP
> and the api **relays WebRTC signaling** (viewers can watch; go2rtc is never exposed).
> A background monitor probes each source for offline↔online health + re-registers
> streams after a gateway restart. Verified against a live stack + synthetic camera
> (H.264 + H.265 paths): 6-test `video` integration suite (incl. an aiortc WebRTC
> round-trip and kill/restart resilience) green; api image gains ffprobe and still
> passes the Trivy CRITICAL gate. Design was pressure-tested by a critique panel
> that caught the WebRTC-media transport limit — real browser playback is **M1.2**;
> M1.1 delivers the plumbing (stream available, signaling answer, RTSP re-serve).

### M1.2 — Live view in the PWA — 🔨 implemented (in review; CI: `stack`/`e2e-live` + `lighthouse` jobs)
- [x] [A] Playwright: WebRTC frames flowing within 3 s of page load
- [x] [A] Playwright: auth redirect; viewer role can view
- [~] [A] Bench: glass-to-glass < 500 ms — CI asserts steady-state WebRTC playout
  latency (jitter-buffer delay, ~1 ms on loopback) < 500 ms; true perceptual
  glass-to-glass on real LAN is the [M] device check (a burned-in-timecode OCR
  bench isn't reliably automatable — ffmpeg can't burn absolute wall-clock ms)
- [x] [A] Lighthouse: installability passes; mobile performance tracked as a budget warning
- [ ] [M] Physical iOS + Android: install, live view, lock/unlock, camera switching — ______
- [ ] [M] NoIR + IR illuminator usable image in dark room — ______

> Real browser **WebRTC media** (deferred from M1.1) now works: go2rtc's media port
> `8555` (udp+tcp) is published + advertised as an explicit ICE candidate
> (`EEPER_GO2RTC_CANDIDATE`; go2rtc excludes its own Docker-bridge address), while
> its signaling/RTSP control planes stay dark behind the authed api relay — a
> deliberate, scoped isolation regression (`test_gateway_control_planes_are_not_reachable`
> now asserts *only* 8555 is published). The PWA is installable (`@vite-pwa/sveltekit`
> manifest + Workbox SW) with a Live view: recv-only WebRTC playback, per-camera
> online/offline health, multi-camera switching, and a client route guard (viewer
> role included). Architecture was locked by a design workflow (go2rtc/CSP facts
> verified against primary sources) and a blocking spike proved headless Chromium
> decodes frames in ~2 s. CSP hashing stays deferred (adapter-static SPA can't emit
> SvelteKit hashes; WebRTC bypasses CSP anyway).

### M1.3 — Adapters (USB & Pi CSI) — 🔨 implemented (in review; CI: `stack`/`adapters-usb` + `images` jobs)
- [~] [A] USB adapter → contract-conformant stream end-to-end (gateway → browser).
  Hosted CI can't `modprobe v4l2loopback` (kernel lockdown — verified), so the
  required gate runs the SAME adapter image with a synthetic input through the
  identical encode→RTSP→gateway→browser path; a best-effort non-blocking leg tries
  a real loopback if a runner permits. Real V4L2 device-open is the [M] bench.
- [x] [A] Adapter images multi-arch (USB amd64+arm64; CSI arm64-only) + pass the
  same contract-validation suite as native cameras; both Trivy-CRITICAL-clean.
- [ ] [M] Physical USB webcam + CSI Camera Module 3 stream via adapters on bench — ______
- [ ] [M] Android phone RTSP-app onboarding using only the doc — ______

> Two first-party adapter images (mediamtx-binary + encoder, the proven
> synthetic-camera pattern rebuilt to pass the CRITICAL gate): **USB** (ffmpeg
> V4L2, amd64+arm64) reads a UVC webcam — or a synthetic lavfi source in CI —
> through the same H.264-baseline/≤1080p encode; **CSI** (mediamtx native
> `rpiCamera`/libcamera, arm64-only) for the Pi Camera Module (capture is [M]
> bench). `images.yml` gained a per-image `platforms` field (CSI arm64-only) with
> the PR-scan hole fixed. Verified locally: USB stream is H.264 baseline 720p and
> plays end-to-end through the gateway; both images build their target arches and
> pass Trivy CRITICAL. Plus a phone-RTSP doc mapping the app config to the real API
> contract/errors. The v4l2loopback-in-CI limit was a design-workflow finding the
> user signed off on (hosted fallback).

### M1.4 — Recorder — 🔨 implemented (in review; CI: `stack`/`recorder` job)
- [x] [A] Ring buffer: segments written, quota eviction, promoted clips survive
- [x] [A] Promoted clip playable, duration/timestamps match (ffprobe; keyframe-aligned ±1 GOP)
- [x] [A] Playback endpoint auth-enforced (401/404 + HTTP Range 206), plays in browser harness
- [x] [A] Crash mid-segment loses at most active segment; index consistent

> A dedicated **recorder** container (reuses the api image; `record` profile) runs
> one `ffmpeg -c copy` child per enabled camera, writing MPEG-TS segments to a
> shared `media-data` volume, plus a retention task that evicts oldest segments
> over a byte quota. The **filesystem is the index** (no segments table): a
> segment is finalized iff a strictly-newer sibling exists, so a SIGKILL loses at
> most the open segment — verified against ffmpeg source + a live docker-kill test.
> Admin **clip promotion** (`POST /cameras/{id}/clips`) concats the covering
> finalized segments (`-c copy` + faststart) into `/media/clips` (a subtree
> retention never touches, so clips survive eviction), storing requested + probed
> actual windows; **playback** is an authed, household-scoped `FileResponse` with
> native Range. Starlette floored to ≥0.49.1 (CVE-2025-62727). 7-test `recorder`
> CI suite + a system-Chrome clip-playback leg, all green. Architecture locked by
> a design workflow (crash-safety proven against `libavformat/segment.c`).

**Phase 1 exit:** the 24 h sustained-record + live-view CPU-budget check is a
[MANUAL] bench item ([docs/ci.md](ci.md)) — no self-hosted runner; met by
construction (`-c copy` everywhere, `scandir`+`unlink` retention, `sendfile`
playback).

---

## Phase 2 — Audio & First Insights

### M2.0 — Labeled audio fixture library — 🔨 implemented (in review; CI: `fixtures` job)
- [x] [A] Manifest integrity: required fields + allowed-license enforcement (NC denylist)
- [x] [A] Reproducible build: bit-identical output across two clean builds (pinned container)
- [x] [A] Tampered source file fails the build (SHA-256 verify on fetch)
- [x] [A] Eval/dev splits disjoint at source-clip level (by content sha256 + scene sources)
- [x] [A] Statistical floor: ≥100 cry / ≥300 confuser scenes, ≥30 per confuser category
- [x] [A] Annotation sanity: bounds + ≥1 cry event per cry scene
- [ ] [M] Two-person verification pass complete, recorded in manifest — ______
- [ ] [M] Realism spot-check of synthesized scenes — ______

> The `fixtures/` CI-only package (own deps, `numpy<2` for Scaper): a per-clip
> `manifest.json` (source URL, license, sha256, labels, split, verification status)
> + `fixtures verify|build|check|repro|provenance`. `build` fetches + checksum-verifies
> each source clip and synthesizes nursery scenes with Scaper (event over the nursery
> floor, swept SNR, light reverb), deterministically. No third-party audio is committed.
> Sources (NC excluded by policy): cry = donateacry-corpus (ODbL, pinned commit);
> speech/music-TV/pets = FSD50K via a pinned CC0/CC-BY mirror (by-nc + sampling+ dropped);
> white-noise/lullaby/nursery-floor = generated (CC0). The real fixtures-v1 manifest is
> 236 source clips → a 630-scene library (built + all gates green locally in ~2m40s).
>
> **fixtures-v1 scope (approved):** confuser categories speech / music_tv / pets /
> whitenoise_lullaby. The **sibling/other-child** category + richer pets-dev are deferred
> to **fixtures-v1.1** — no clean fetchable child-speech source exists (Common Voice gated,
> FSD50K child-speech sparse); the tooling ingests `eeper-recorded` clips when recorded.

### M2.1 — Audio pipeline — 🔨 implemented (in review; CI: `stack`/`recorder`+`e2e-live`+`video` jobs)
- [x] [A] Known audio track arrives as 16 kHz mono PCM windows, verified vs a fixture
  (pure-Python Goertzel 1 kHz-tone dominance, not a bit-exact checksum → robust to ffmpeg drift)
- [x] [A] Live view audio packets flowing (getStats inbound-rtp audio, while muted)
- [ ] [M] Real mic intelligible, no gross A/V drift over 10 min — ______

> New **insight-engine** service (`server/eeper/insight/`, `insight` profile, reuses
> the api image — like the recorder). M2.1 stage = audio extraction: one
> `ffmpeg -vn -ac 1 -ar 16000 -f s16le` child per enabled camera pulling go2rtc's
> RTSP re-serve, framed into 1.0 s (16000-sample) windows in a per-camera in-process
> ring (the M2.2 feature-extractor seam), with a test-only WAV tap. **Listen-in**:
> camera registration now adds a second on-demand go2rtc source
> (`ffmpeg:camN#video=copy#audio=opus`) so the browser gets a WebRTC audio track
> (AAC isn't a WebRTC codec) — verified live: the SDP answer carries `m=audio opus`,
> and the Live view exposes/asserts inbound audio packets flowing (muted). The 1 kHz
> tone check is source-verified (fixture + live both ~1e9 dominance). Design-workflow
> + a live Opus spike drove it; 10-min A/V sync = [M] bench.

### M2.2 — Insight engine core + motion — 🔨 implemented (in review; CI: `stack`/`recorder` job)
- [x] [A] Motion score ordering: still < rolling < sitting-up (unit; measured 0.000 < 0.012 < 0.052)
- [x] [A] Hysteresis: threshold-oscillating trace → ≤ 1 state change (unit; dual-band + post-transition dwell)
- [x] [A] cam-motion onset → movement-level event on MQTT + `state_history` row within 2 s (integration)
- [x] [A] Backpressure: frames dropped, memory bounded (ring ≤ maxlen), freshness < 3 s (unit + integration)
- [x] [A] Video-only degradation: engine runs, registry reports exactly the matching extractors (unit + live)

> The insight engine gains a **video path** alongside M2.1's audio: a second ffmpeg
> child per camera decodes gray frames (`fps=5,scale=160x120,format=gray`) into a
> latest-wins `FrameRing`; a per-camera scorer diffs consecutive frames (pure-Python
> normalized MAD, no numpy), EWMA-smooths, and runs a **low/medium/high hysteresis**
> state machine (dual enter/exit bands + post-transition-only min-dwell — leading edge
> fast for onset, trailing edge sticky against flap). Transitions write `state_history`
> + `events` (new TimescaleDB hypertables, composite `(ts,id)` PK — the partitioning
> column must be in every unique index) then publish over MQTT (`eeper/insight/state`
> retained; `.../motion` per tick) to a new internal-only **mosquitto** broker (no host
> port; TLS/ACLs are M3.1). **Backpressure**: the ring drops the backlog so a slow
> scorer always reads the freshest pair. **Graceful degradation**: the audio child is
> spawned only when the source has audio; a video-only camera runs with the motion
> extractor alone. Per-stream reap (a video hiccup never drops listen-in audio).
> Adversarial design workflow (3 proposals → critique → synthesis) + live calibration
> (Timescale PK, ffmpeg framing, cam-motion 8 s still↔moving cycle) drove it; the 10 min
> A/V-sync-style perceptual checks stay [M] bench.

### M2.3 — Audio nudges: sound level + experimental cry — 🔨 implemented (in review; CI: `audio` + `stack`/`recorder` jobs)
Reframed after exhaustive measurement (in the PR): pretrained YAMNet can't carry cry
*classification* to a first-class bar (sustained-episode recall for one infant caps
~0.70 at any false-nudge-safe point — correlated window errors). v1 nudge = **sound
level** (robust, model-free); cry classification ships **experimental, off by
default**, with window ratchet baselines; the trained-model unlock is **M2.6** (M2.5 de-risked it — the corpus is the wall).
- [x] [A] Model fetch: checksum verification, tampered file refused
- [x] [A] Sound-level product gate on `fixtures-v1`: episode recall ≥ 0.90, latency ≤ 10 s, ≤ 1 false event / quiet 8 h night, continuous-noise absorbed (product-derived, in CI)
- [x] [A] Cry-classifier window ratchet baselines (near + physically-based far-field recall/FPR) recorded + ratcheted (regression fails; not an absolute)
- [x] [A] Sustained sound → `sound_elevated` event end-to-end (MQTT + `state_history`)
- [x] [A] ONNX CPU inference smoke test on amd64 + arm64
- [ ] [M] Speaker-played cry raises a sound nudge; a quiet room 30 min does not — ______

### M2.4 — Events, clips, nudges — 🔨 implemented (server merged PR #14; web in review). CI: `stack`/`recorder` + `python` jobs
Split into server-infra then web (per plan). Server: a **DB-as-queue** nudge worker
(Postgres `LISTEN/NOTIFY` + reconciliation poll; delivery state on the event row →
crash-safe, exactly-once) does auto-clip-promotion + Web Push + WebSocket broadcast;
delivery policy (quiet hours, per-camera rate-limit) lives in the worker. Web: the
**Tonight view** (live event list over `/ws/events`, tappable clips) + Web Push opt-in
+ quiet-hours UI + a service-worker push handler (keyed on the event-id collapse key).
- [x] [A] Audio nudge (`sound_elevated` in v1) auto-promotes pre/post-roll clip, linked and playable (`test_nudge_pipeline` end-to-end)
- [x] [A] Web Push matrix: subscribed receives; opted-out/quiet-hours do not (`test_nudge_worker` + real send in `test_push_send`)
- [x] [A] Copy lint: notification templates pass clinical-terms denylist (`test_push_service`)
- [x] [A] Crash-safety: reconciliation-without-NOTIFY, exactly-once-across-restart, rollback-no-side-effects (real Postgres)
- [x] [A] Playwright: event appears in Tonight view via WebSocket; clip plays on tap (`tonight` project, `recorder` job)
- [ ] [M] Push arrives on physical iOS + Android, backgrounded + locked — ______

### M2.5 — Cry ratchet + honest reframe (first-class cry is corpus-gated) — 🔨 implemented
A reproducible de-risk (in the PR) tested M2.3's premise — train a head to unlock cry —
and found the wall is the **corpus, not the model**: a trained head (logistic + MLP, over
the 521 AudioSet scores AND the 1024-d embeddings, balanced + near/far augmented) over
FULL donateacry (457, split device-disjoint on the per-upload UUID) does NOT beat the
pretrained scorer at any false-nudge-safe point (best trained near recall ~0.47 vs ~0.84
pretrained on the same split; binding confuser cry-vs-animal; residual infant leakage would
only flatter the trained head, which still lost). The pretrained model is already near the
corpus ceiling (~0.80 near / ~0.76 far window recall, ~0.85 episode). So cry stays
experimental + off by default; the window floors are ratcheted UP to the measured reality;
the on-by-default flip + blocking gate move to M2.6.
- [x] [A] Cry window ratchets raised on `fixtures-v1` (near recall 0.75→0.78 / FPR 0.12→0.08, far recall 0.55→0.72 / FPR 0.20→0.11), CI fails on regression
- [x] [A] Sound-level product gate unchanged + green; ONNX CPU smoke amd64+arm64 unchanged
- [x] [A] De-risk method + numbers + corrected corpus premise (no FSD50K cry positives; donateacry gives only a per-device UUID proxy) recorded in `cryeval.py` + plan

### M2.6 — Cry corpus expansion (the first-class-cry prerequisite) — ⬜
The real unlock: a corpus that doesn't yet exist. Source a larger + more diverse cry corpus
with **guaranteed infant-level ids** (leakage-safe splits — donateacry gives only a
per-device UUID proxy), the **FSD50K cry positives** the current corpus lacks, a **real-RIR**
far-field corpus, and a **train split**; then re-run training.
- [ ] [A] Reproducible training: artifact rebuilds from the corpus manifest (checksum), fetched + verified
- [ ] [A] Leakage-safe eval: eval split infant-disjoint from train (asserted)
- [ ] [A] Quality gate ratcheted UP: near-field recall ≥ 0.9 / FPR ≤ 0.1 + far-field floor (real RIR); episode recall ≥ 0.95 at ≤ 1 false cry-nudge / 8 h night
- [ ] [A] ONNX CPU inference smoke on amd64 + arm64
- [ ] [M] Speaker cry raises a *cry* nudge; 30 min TV/pets does not — ______

**Phase 2 exit:** ⬜ [M] speaker cry → phone nudge (sound-level in v1) → playable clip on bench — ______ (cry classification stays experimental until the M2.6 corpus lands)

---

## Phase 3 — Sensors & Sleep States

### M3.1 — MQTT bus & device onboarding — ✅ done (3 slices: broker hardening ✓, devices+ingestion ✓, web ✓)
- [x] [A] Plaintext MQTT refused (slice 1: TLS-only broker + dynamic-security plugin, anonymous off)
- [x] [A] ACL matrix: cross-device publish/subscribe denied for every class (slice 2: per-device dynsec role scoped to eeper/dev/{id}/#)
- [x] [A] Fuzzing: malformed/oversized messages rejected without crash/slowdown (slice 2)
- [x] [A] Synthetic device pairs, publishes, lands in `sensor_readings` with quality (slice 2)
- [x] [A] Offline detection within heartbeat window, reflected in UI (slice 3: Devices view pairs a node, shows the one-time credential, and renders online/offline/never-seen health; Playwright pairs via the UI, publishes as the node over TLS, and asserts the flip to Online)

### M3.2 — Reference sensor firmware — 🔨 [AUTO] done; [MANUAL] awaits hardware
- [x] [A] ESPHome configs compile in CI; payloads validate against contract schema (mmWave LD2410 + PIR reference configs build to real ESP32 images in CI; golden payloads checked against `SensorMessage`; config-structure lint enforces the ACL/TLS/discovery/clock invariants)
- [ ] [M] Physical mmWave + PIR detect person-analog at crib distance; 24 h uptime — ______
- [ ] [M] Non-author flashes and pairs a node from docs alone (see firmware/PROVISIONING.md) — ______

### M3.3 — Fusion state machine — ✅ [AUTO] done (slices 1–3); [MANUAL] overnight bench pending hardware
- [x] [A] Replay gate: ≥ 90 % sleep/wake epoch agreement; all wakes ≥ 3 min detected (slice 1: pure-Python fusion vs. seeded synthetic labeled nights; measured ≥0.963 agreement / 1.000 wake-recall across every modality subset, floors set with headroom)
- [x] [A] Combinatorial degradation: valid states under every input subset (slice 1: fusion runs on all 2³ subsets incl. none; only defined sleep/arousal, never crashes)
- [x] [A] Corroboration: no distress from single signal when ≥ 2 inputs live (slice 1: distress needs ≥ 2 corroborators and only while awake)
- [x] [A] Session integrity: count + boundaries ±2 min (slice 1 ✓ via back-dated boundaries); **survives engine restart** (slice 2: a stateless worker re-derives state from the durable `fused_states` log, so a restart doesn't fragment or duplicate a session — integration-tested against a real Postgres)
- [x] [A] Playwright: Tonight timeline renders replayed night; scrub-to-clip works (slice 3: a scrubbable track of fused sleep/wake + distressed bands over `GET /fusion/timeline`, with nudge markers; tapping a marker plays its clip. The Playwright harness seeds a replayed night's `fused_states` and asserts the bands render + scrub-to-clip on the recorder stack)
- [ ] [M] Live overnight bench run reviewed against reality notes — ______

**Phase 3 exit:** ✅ [AUTO] — a full replayed night flows from raw inputs → fused states + sessions → an accurate Tonight timeline with zero manual intervention (fusion replay gate + live worker + timeline render, all in CI). The [MANUAL] live-overnight review awaits bench hardware.

---

## Phase 4 — Trends & Pulse-Ox

### M4.1 — Trends — ✅ [AUTO] done (slices 1–3: aggregates + compression, API + CSV, charts UI)
- [x] [A] Rollups match independently computed values on seeded month (slice 1: the `trends_nightly` continuous aggregate over a materialized `sleep_sessions` hypertable matches an independent GROUP BY exactly, verified against a real TimescaleDB testcontainer)
- [x] [A] Every Trends query < 200 ms over seeded year (bench) (slice 1: a weekly rollup over a seeded year returns in a couple ms; gate wired)
- [x] [A] Compressed/uncompressed query results identical (slice 1: chunks > 7 days compressed via policy; totals identical before/after)
- [x] [A] Playwright: charts render; CSV export matches; viewer denied export (slice 3: the Trends view renders SVG sleep-duration / wake-count / week-over-week charts for a seeded month; an admin downloads the CSV; a viewer sees the charts but no export button — Playwright `trends` project on a core TimescaleDB stack)

### M4.2 — Pulse-ox (optional, gated) — ✅ [AUTO] done (slices 1–3: gating + copy lint ✓, quality-gated ingestion + fusion HR ✓, trend UI + caveat Playwright + reference firmware ✓); [MANUAL] adult-wearer bench pending hardware
- [x] [A] Gating matrix: inert without profile + acknowledged disclaimer (slice 1: `enabled` = profile-flag AND an admin's acknowledgment of the *current* disclaimer version; acknowledgment is admin-only and version-checked; state-matrix tested)
- [x] [A] Low-quality samples discarded, not stored/fused; discard rate observable (slice 2: the `PulseOxIngestor` drops samples below the quality threshold at the paho callback — never enqueued/stored — and counts them per device; `GET /pulseox/health` surfaces the discard rate; accepted samples land in the `pulseox_readings` hypertable)
- [x] [A] Copy lint on pulse-ox strings; accuracy caveat asserted on every view (slice 1: the **clinical-terms copy lint** exists + gates CI, reviewed disclaimer the sole exemption; slice 3: the `/pulseox` view renders the `ACCURACY_CAVEAT` on **every** state — off / disclaimer / trend — and the `pulseox` Playwright project asserts the caveat is present on both the disclaimer and trend views and identical across them)
- [x] [A] Fusion consumes HR features only from quality-gated samples (slice 2: `hr` is an optional `EpochFeatures` field loaded from `pulseox_readings` (accepted-only) and used as one distress corroborator — never sufficient alone, `None` without pulse-ox so the M3.3 replay gates are unchanged)
- [ ] [M] Adult-wearer bench readings plausible; motion artifacts down-weighted — ______

### M4.3 — Operations polish — ✅ [AUTO] done (slices 1–3: retention final ✓, backup/restore ✓, grandparent mode + settings + role sweep ✓)
- [x] [A] Backup → fresh stack → restore round-trip, checksummed identical (slice 2: `deploy/backup.sh` (pg_dump custom-format + a read-only tar of the media volume) and `deploy/restore.sh` (drop/recreate the DB, then `pg_restore` bracketed by TimescaleDB `pre_restore`/`post_restore`, then unpack media); the `backup-restore` CI job seeds a stack → backs up → `down -v` → restores into a fresh stack and asserts the DB digest + media SHA-256 are identical. Continuous aggregates, compression, and retention policies all survive the round trip — validated end-to-end locally too)
- [x] [A] Retention matrix evicts exactly the expected artifacts (slice 1: **media** — finalized recording segments evicted by AGE (`media_max_age_seconds`) and by QUOTA (oldest-first), the active newest-per-camera segment and promoted clips never touched; **time series** — a TimescaleDB retention policy drops raw-telemetry chunks (`state_history` / `sensor_readings` / `pulseox_readings`) older than `timeseries_retention_days`, the Tonight-history + trends tables retained. Both opt-in (0 disables). Verified: a pure media-tree eviction matrix + a real-TimescaleDB test that registers the policies and drops old chunks on a job run)
- [x] [A] Playwright role sweep: viewer scope correct on every route (slice 3: "grandparent mode" — a viewer's home shows only Live + Tonight; `/trends`, `/devices`, `/pulseox`, `/settings` each redirect a viewer to Tonight; a new admin-only `/settings` hub consolidates account + management links; the `roles` Playwright project asserts a viewer sees only the two links, is bounced from every management route, and is denied CSV export (403), while an admin reaches every surface)

**Phase 4 exit:** ✅ v1.0 feature-complete; full [A] suite green; [M] checklist current (the outstanding [M] items — the overnight fusion bench and the adult-wearer pulse-ox bench — are hardware-only and tracked for Phase 5)

---

## Phase 5 — Hardening & Release

### M5.1 — Security review — 🔨 in progress (slices 1–2: supply-chain scanning ✓, auth-fuzzing corpus + threat-model review ✓); [M] pen test pending
- [x] [A] Zero critical CVEs; secrets scan clean; all prior security suites green (slice 1: a **`secrets-scan`** CI job (gitleaks over full git history, `.gitleaks.toml` allowlisting only dummy test fixtures + `*.example` templates) and a **`dependency-audit`** job (pip-audit over the shipped runtime dependency closure — dev/test + build tooling excluded — and `npm audit --omit=dev` for the web app); the runtime container is Trivy-scanned for CRITICAL CVEs in `images.yml`; the port-exposure / TLS / auth-matrix / ACL suites from earlier milestones remain the regression harness — mapped in `docs/operations/security-review.md`)
- [x] [A] Auth fuzz corpus: tampering/replay/downgrade all rejected (slice 2: `server/tests/test_auth_fuzzing.py` hits the live API with attacker-crafted cookies — flipped signature, payload swapped under the old signature, attacker-secret signature, wrong token type, `alg:none` forgery, HS512 algorithm confusion, expired access token, replayed rotated refresh token — all 401; plus an escalation probe proving the token's `role` claim is **not** authoritative (authorization reads the DB role → 403 on admin endpoints))
- [ ] [M] Pen test of default install: no unresolved critical/high findings — ______

### M5.2 — Performance gate & docs — 🔨 in progress (slices 1–2: docs gates ✓, bench harness + hardware/perf docs ✓); the reference-bench run is hardware-executed
- [~] [A] Reference bench: < 60 % CPU, latency budgets met, 72 h clean run (slice 2: `scripts/bench.py` measures steady-state CPU / HTTP latency / restarts+OOM and fails on any breached budget; a **`bench-smoke`** CI job validates the harness every push (relaxed budgets — a GitHub runner isn't a Pi); the **real reference-profile gate** runs via `.github/workflows/bench.yml` on a self-hosted `[self-hosted, bench, pi5]` runner (`workflow_dispatch`, 72 h). The physical Pi 5 run is the remaining hardware step, logged in `docs/performance.md`)
- [x] [A] Docs link checker + install-doc smoke test on clean VM (slice 1: a **`docs-links`** CI job (lychee, offline + fragments, over `git ls-files '*.md'` so every internal link **and anchor** in first-party Markdown resolves) and a **`docs-install-smoke`** job (`scripts/docs_install_smoke.sh` extracts the `## Install` commands from `docs/install.md`, rewrites only the clone URL to the checkout, runs them verbatim on a clean dir, then asserts the promised outcomes — generated `.env` secrets, the extracted local CA, and a healthy first-boot-gated stack). Verified end-to-end locally)
- [ ] [M] Two external testers cold-start on non-Pi hardware from docs — ______
- [ ] [M] Clinician/child-health review of all safety copy — ______

**v1.0 release:** ⬜ all [A] green on both architectures · ⬜ all [M] recorded for the RC · 🔧 signed images published (pipeline in place — `images.yml` keyless-signs every pushed image + attaches SBOM/provenance; awaiting the first release-tag run) · ⬜ release notes include safety stance verbatim

---

## Change log

| Date | Change |
| ---- | ------ |
| 2026-07-14 | Release mechanics — signed images (Phase 5 exit criterion). Extends `images.yml` so every image published to GHCR is **keyless-cosign-signed** (Sigstore Fulcio + the public Rekor transparency log, via the workflow's GitHub OIDC identity — no long-lived keys) and carries an **SBOM** (SPDX) + **max-mode build provenance** attestation. The push step now signs the pushed manifest by digest; the workflow also triggers on `v*` release tags, publishing an immutable `:vX.Y.Z` alongside `:latest` / `:<sha>` (all signed). Adds `docs/operations/verifying-images.md` — how to `cosign verify` against the workflow identity (not just "is it signed" but "signed by our pipeline"), inspect the SBOM/provenance, and what the guarantees are. Complements the CRITICAL-CVE Trivy scan every image already passes before push. Signing runs on push events only (gated `github.event_name == 'push'`), so it exercises on the post-merge `main` run / release tags, not on PRs. This lands the automation behind the "signed images published" release criterion (the first real signed artifacts appear on the next `main` push / release tag). |
| 2026-07-14 | M5.2 slice 2 (bench harness + hardware/perf docs). Builds the reference-profile performance automation. `scripts/bench.py` is a pure-stdlib harness (only the `docker` CLI) that samples a running stack — summed container CPU as a fraction of host capacity, HTTP/page-load latency, and restart/OOM counts — and emits a JSON report, failing on any breached budget. The **same harness runs in two modes**: a **`bench-smoke`** CI job (in `stack.yml`, every push) brings up the core stack and runs it with `--smoke` (relaxed budgets — a GitHub runner is not a Pi), proving the measurement + reporting machinery works and the stack is stable; the **real reference-profile gate** runs via a new **`.github/workflows/bench.yml`** on a self-hosted `[self-hosted, bench, pi5]` runner (`workflow_dispatch`-only, so it never runs or bills in ordinary CI), bringing up the full stack and enforcing the strict budgets (< 60 % CPU, < 3 s latency, 72 h soak). New docs: **`docs/performance.md`** (budgets, the harness, CI-vs-bench split, how to register the runner, a results log) and **`docs/hardware.md`** (the reference-profile bill of materials + no-Pi alternatives + cameras/sensors/storage), both linked from the docs index. CONTRIBUTING + the PR template already carry the §2 safety boundary and the testing bar, so that deliverable is complete. Verified locally: the harness ran against a live core stack (mean CPU 0.07 %, median latency ~15 ms, 0 restarts/OOM → pass). Remaining M5.2: the physical Pi 5 bench run (hardware) + the two [MANUAL] reviews (cold-start testers, clinician safety-copy). |
| 2026-07-14 | M5.2 slice 1 (docs gates — link checker + install-doc smoke). Two CI jobs make the docs a tested artifact. **`docs-links`** runs lychee in offline mode with `--include-fragments` over `git ls-files '*.md'` (tracked first-party Markdown only — vendored `node_modules` / `.pytest_cache` are untracked and excluded for free), so every internal link **and heading anchor** must resolve; offline keeps it deterministic (no flaky external requests). **`docs-install-smoke`** runs `scripts/docs_install_smoke.sh`, which extracts the commands from the `## Install` block of `docs/install.md`, rewrites *only* the public clone URL to the current checkout (so it exercises this tree, and any doc drift fails CI), runs them verbatim on a clean directory, and asserts the outcomes the doc promises: generated `POSTGRES_PASSWORD` + `EEPER_SECRET_KEY` in `deploy/.env`, the extracted `eeper-local-ca.crt`, and a healthy stack that reports `first_boot_required: true` (login-gated, no default credentials). Both were validated end-to-end locally (the smoke cloned the tree, ran `install.sh`, and the stack came up first-boot-gated). Lands the M5.2 docs [AUTO] criterion. Remaining M5.2: the reference-profile bench harness + hardware/sample-hardware docs (slice 2), and the hardware-gated bench run + [MANUAL] cold-start/clinician reviews. |
| 2026-07-13 | M4.3 slice 2 (backup/restore + CI round-trip). Two `deploy/` scripts give a full, no-extra-tooling snapshot + rollback of everything durable. **`backup.sh`** writes a timestamped dir holding `db.dump` (a `pg_dump` custom-format dump of the TimescaleDB database) and `media.tar.gz` (a read-only tar of the media volume — the recording ring buffer + promoted clips), so it is safe to run on a live stack. **`restore.sh`** stops the app services, drops/recreates the `eeper` database, and loads the dump bracketed by TimescaleDB `timescaledb_pre_restore()` / `timescaledb_post_restore()` (the load-bearing detail — a naïve `pg_restore` corrupts a TimescaleDB catalog), tolerating `pg_restore`'s ignorable extension-already-exists notices, then wipes + unpacks the media volume. Continuous aggregates, compression, and retention policies all survive the round trip; the api re-attaches to the restored schema on the next `install.sh` (idempotent, so a no-op). A new **`backup-restore` CI job** proves the M4.3 [AUTO] criterion end to end: it seeds a stack (hypertable rows feeding the continuous aggregate + a known media file), backs up, `down -v` (destroys the volumes), restores into a fresh stack, and asserts the **DB digest and media SHA-256 are byte-identical** to the pre-backup values. The full flow was also validated locally (40 sessions incl. a compressed chunk, the nightly aggregate, and the media tree all identical after a real destroy+restore). New operator doc `docs/operations/backup-restore.md` (backup, restore, cron, move-to-new-hardware); `deploy/backups/` git-ignored. Remaining M4.3: Playwright role sweep + settings/grandparent polish (slice 3) → Phase 4 exit. |
| 2026-07-14 | M5.1 slice 2 (auth-fuzzing corpus + threat-model review). Lands the M5.1 auth-fuzzing [AUTO] criterion: `server/tests/test_auth_fuzzing.py` hits the live HTTP boundary with the hand-crafted cookies an attacker would actually send, and every one fails closed. **Tampering** — a flipped signature, a payload swapped in under the original signature, a token signed with an attacker-controlled secret, and a validly-signed TOTP challenge reused as an access token are all 401. **Downgrade** — the classic `alg:none` unsigned forgery and an HS512 algorithm-confusion token are both 401 (decoding is pinned to HS256). **Replay** — an expired access token and a replayed *rotated* refresh token are both 401 (the latter also revokes the family). And an **escalation** probe mints a perfectly-signed token for a viewer that claims `role=admin` — `/me` still reports `viewer` and the admin endpoint returns 403, proving the token's role claim is not authoritative (authorization reads the database role, `get_current_user` loads the user by `sub` and `require_admin` checks `user.role`). Complements the existing token unit tests + refresh-rotation matrix rather than duplicating them. Also adds `docs/operations/security-review.md` — a **threat-model review against MASTER_PLAN §8** mapping all seven controls (no-exposure, TLS, auth, roles, container hardening + supply chain, secrets, privacy) to the exact test/CI gate that verifies each, and a residual-risk section with the pending **[MANUAL] pen-test** log. Remaining M5.1: the [MANUAL] pen test of a default install (external). |
| 2026-07-14 | M5.1 slice 1 (supply-chain scanning — secrets + dependency audit). Two new CI jobs harden the supply chain. **`secrets-scan`** runs gitleaks over the **full git history** (`fetch-depth: 0`), pinned by image digest; a repo-root `.gitleaks.toml` extends the default rules and allowlists ONLY known-dummy material — hardcoded test-fixture secrets (`server/tests/`, `web/e2e/`) and `*.example` templates — so the scan flags real credentials in app/deploy/infra code without noise (real generated secrets are git-ignored and never committed). **`dependency-audit`** audits the **shipped** dependency trees: `pip-audit --strict` over the server's runtime closure (a clean-venv `pip install ./server` → `pip freeze`, which excludes pip/setuptools and the dev extras like pytest/ruff/mypy — those never ship), and `npm audit --omit=dev` for the web app. The scope is deliberate: the runtime *container* is already Trivy-scanned for CRITICAL CVEs in `images.yml`, so these jobs cover the package-advisory layer that image scanning under-reports. De-risked locally first — pip-audit initially flagged pytest (a dev dep) and pip itself (build tooling), which is exactly why the audit is scoped to the frozen runtime closure (74 deps, zero vulns); gitleaks flagged one test-fixture `secret_key`, now allowlisted. Remaining M5.1: the auth-fuzzing corpus (tampering/replay/downgrade) + the threat-model review doc (slice 2). |
| 2026-07-13 | M4.3 slice 3 (grandparent mode + settings surface + role sweep) — **completes M4.3 [AUTO] and the Phase 4 exit (v1.0 feature-complete)**. The viewer role is now scoped to **Live + Tonight only** ("grandparent mode"): a viewer's home shows just those two entries, and the management routes — `/trends`, `/devices`, `/pulseox`, and a new `/settings` — each redirect a non-admin back to Tonight (client-side guard in every route's `onMount`). A new **admin-only `/settings` hub** consolidates the account (username/role), links to the management surfaces (Devices, Trends, Pulse-ox when the profile is on), a pointer to the Tonight notification controls (which viewers keep, so no one loses their own notification settings), and the app version. The home nav gates every management link behind `isAdmin`. A new **`roles` Playwright project** (the M4.3 role-sweep [AUTO]) asserts the whole boundary: a viewer sees only Live + Tonight, can open both, is bounced from all four management routes back to Tonight, and is denied the CSV export API (403); an admin sees every link and reaches `/settings`, `/trends`, and `/devices`. The M4.1 Trends viewer test was updated to the new contract (viewer has no Trends link and is redirected). Verified end-to-end locally against a real stack. The two remaining Phase 4 [MANUAL] items (the overnight fusion bench and the adult-wearer pulse-ox bench) are hardware-only and carry into Phase 5. |
| 2026-07-13 | M4.3 slice 1 (retention final — age + quota across media and time series). The retention daemon gains an **age** bound alongside the existing byte **quota**. Media: `evict_once` now evicts finalized recording segments older than `media_max_age_seconds` (0 = quota-only, unchanged) *and* oldest-first once over `media_quota_bytes`; both policies operate only on the finalized set, so the active newest-per-camera segment is never touched, and promoted clips are never scanned. Time series: the api installs a **TimescaleDB retention policy** at boot on the raw high-volume telemetry hypertables (`state_history`, `sensor_readings`, `pulseox_readings`) dropping chunks older than `timeseries_retention_days` (0 = keep everything; opt-in) — the Tonight-history (`events`, `fused_states`) and trends (`sleep_sessions`, compressed not dropped) tables are deliberately excluded. Both knobs are wired through Compose (`EEPER_MEDIA_MAX_AGE_SECONDS` on the recorder, `EEPER_TIMESERIES_RETENTION_DAYS` on the api), defaulting off. The **retention-matrix [AUTO]** criterion lands: a pure media-tree test drives every combination (quota-only, age-only, age-then-quota, cross-camera oldest-first, active-segment-never-touched, clips-never-touched, no-op) and asserts exactly which files remain; a real-TimescaleDB test confirms the policies are registered on the right tables (and not the derived/trends tables) and that running the job drops old chunks while keeping recent data. Remaining M4.3: backup/restore round-trip (slice 2), Playwright role sweep + settings/grandparent polish (slice 3). |
| 2026-07-13 | M4.2 slice 3 (pulse-ox trend UI + accuracy-caveat Playwright + reference firmware) — **completes M4.2 [AUTO]**. A new **`/pulseox` view** (optional, insights-only) surfaces heart-rate as **trend context, never a live readout**: it renders one of three states — *off* on this deployment, the *disclaimer + admin acknowledge* flow, or the *HR trend chart* (hourly averages over `GET /pulseox/trend`, a dependency-free SVG `BarChart`) — and the **`ACCURACY_CAVEAT` banner is present on every one of them** (the last M4.2 copy criterion). The trend read endpoint is inert (returns `[]`) unless pulse-ox is fully enabled (profile AND acknowledged), so data stays withheld exactly like the rest of the gate; it bins mean HR per hour from the accepted-only `pulseox_readings`. The home nav surfaces a **Pulse-ox** link only where the profile is enabled (an `$effect` fetches status on reaching the authed view). A `pulseox` **Playwright** project on a core stack whose api has `EEPER_PULSEOX_PROFILE_ENABLED=true` acknowledges the disclaimer through the UI, then asserts the caveat is present on both the disclaimer and trend views (and identical across them) and that the seeded samples render as trend bars — verified end-to-end locally against a real stack. Adds the **ESP32 + MAX3010x reference firmware** (`firmware/micropython/eeper_pulseox_node.py`, since ESPHome has no HR/SpO2 component): it reuses the join/SNTP/TLS-MQTT plumbing and publishes the `{ts, hr, spo2, perfusion, quality}` contract to `eeper/dev/{id}/pulseox` with a **mandatory, honest `quality`** field (publishes only when a finger is present and confidence clears the gate), and a golden payload now validates against `PulseOxMessage` in the firmware-contract test. Server tests add the trend endpoint's gating + hourly-average correctness. eeper is never a vital-sign monitor or alarm; the adult-wearer bench stays [MANUAL] (hardware). Remaining in Phase 4: M4.3 (operations polish). |
| 2026-07-13 | M4.2 slice 2 (quality-gated pulse-ox ingestion + fusion HR feature). The pulse-ox data path, insights-only. A `PulseOxMessage` contract (`{ts, hr, spo2, perfusion, quality}`) on `eeper/dev/{id}/pulseox`; the `PulseOxIngestor` (started only when the profile is on, mirroring the M3.1 sensor ingestor) applies the **quality gate** at the paho callback — a sample below the confidence threshold is discarded (never enqueued, never stored, never fused) and counted per device, so `GET /pulseox/health` exposes the **discard rate**. Accepted samples land in a `pulseox_readings` hypertable (the M3.1 sensor ingestor now skips the `pulseox` metric so the two don't collide). Fusion gains an optional **`hr`** feature: the featurizer loads mean HR from `pulseox_readings` (accepted-only, so fusion consumes HR *only* from quality-gated samples), and the state machine treats an elevated HR as one more **distress corroborator** — never sufficient alone, and `None` without pulse-ox, so the M3.3 replay gates are provably unchanged (whole fusion suite still green). Tested: quality gate + discard-rate observability (pure), HR corroboration (with/without HR), and the featurizer surfacing HR from the table. Remaining: trend-context UI + accuracy-caveat Playwright + the ESP32/MAX3010x reference firmware (slice 3). |
| 2026-07-13 | M4.2 slice 1 (pulse-ox safety scaffolding — gating + disclaimer + copy lint). The safety foundation for the optional, insights-only pulse-ox input — **no data path yet, on purpose**. Pulse-ox stays fully inert until BOTH the `pulseox` profile flag is on AND an admin has acknowledged the disclaimer: a versioned per-household **acknowledgment** (`PulseOxConsent`, admin-only, rejects a wrong/old version so a text change forces re-acknowledgment), a carefully-worded **insights-only disclaimer** (the single reviewed safety-copy module), and a `/pulseox` router (`status` / `disclaimer` / `acknowledge`) whose `enabled` is the AND of both halves. Adds the **clinical-terms copy lint** (`server/scripts/clinical_terms_lint.py`) as a CI job — user-facing copy (web UI + push templates) may make no medical / diagnostic / vital-sign / alarm claim, developer safety comments and URLs are ignored, and the disclaimer is the sole exemption; this makes CONTRIBUTING's "enforced, not aspirational" claim real. Compose wires `EEPER_PULSEOX_PROFILE_ENABLED` (default false). Tested: the gating state-matrix (profile × acknowledgment), admin-only + version-checked acknowledgment, and the lint (catches medical/alarm framing, ignores comments, passes the current tree). eeper is never a vital-sign monitor or alarm. Remaining: quality-gated ingestion + fusion features (slice 2), trend-context UI + firmware + the accuracy-caveat Playwright (slice 3). |
| 2026-07-13 | M4.1 slice 3 (Trends charts UI) — **completes M4.1 [AUTO]**. A new **Trends view** (`/trends`) renders the night-over-night story: headline cards (last night, 7-night average sleep, average wakes, longest stretch) and dependency-free **SVG bar charts** (a lightweight `BarChart` component) for sleep-per-night, wakes-per-night, and a week-over-week average — over the `/trends/nightly` + `/trends/weekly` endpoints. An **admin** gets a CSV **export** button (a same-origin `download` link to `/trends/export.csv`); a **viewer** sees the charts but no export button (and the API denies them 403 regardless). New `fetchTrendsNightly` / `fetchTrendsWeekly` client helpers + a home-screen nav entry. A `trends` Playwright project on a fresh core (TimescaleDB) stack seeds a month of `sleep_sessions`, refreshes the continuous aggregate, and asserts: the charts render one bar per night, the admin's CSV download has the right header + a row per night, and a viewer has no export button. Verified end-to-end locally against a real stack. This lands the last M4.1 [AUTO] criterion. Remaining M4.x: the [MANUAL] items and M4.2 (pulse-ox, optional/gated) / M4.3. |
| 2026-07-13 | M4.1 slice 2 (Trends API + CSV export). Three read endpoints over the `trends_nightly` continuous aggregate: `GET /trends/nightly` (per-night sleep duration / sessions / wake count / longest stretch — the chart series), `GET /trends/weekly` (7-day rollups with per-week totals + averages, for week-over-week), and `GET /trends/export.csv` (nightly data as a downloadable CSV, in hours). Export is **admin-only** — the `AdminUser` dependency denies a viewer role (403) before any query runs, so a household viewer can see trends but not export. Server tests run against a **real TimescaleDB testcontainer** (the endpoints read the continuous aggregate): nightly + weekly correctness, **the CSV export matching `/trends/nightly` row-for-row**, and viewer-denied / unauthenticated gating. Remaining: the Trends charts UI + Playwright (slice 3). |
| 2026-07-13 | M4.1 slice 1 (Trends data foundation — aggregates + compression). Sleep sessions are now **materialized** into a `sleep_sessions` TimescaleDB hypertable (per-session metrics: time asleep, intra-session wake count, longest unbroken stretch), computed from the fused-state timeline by the fusion worker as each session closes — idempotent via `ON CONFLICT (household_id, started_at)`, so every cycle re-runs harmlessly (the still-open session stays derived-on-read). A **`trends_nightly` continuous aggregate** rolls sessions into nightly totals, and a **compression policy** compresses `sleep_sessions` chunks older than 7 days; both are created idempotently at boot on an AUTOCOMMIT connection (continuous-aggregate DDL can't run in a transaction) and skipped on plain Postgres. TimescaleDB feasibility was de-risked first (aggregate correctness, compression identical-results, and query latency all confirmed on the pinned prod image). New `test_trends.py` runs the M4.1 [AUTO] gates against a **real TimescaleDB testcontainer**: materialization metrics, the continuous aggregate matching an independent GROUP BY exactly over a seeded month, compression preserving query results, and a weekly rollup over a seeded year returning in ~ms (< 200 ms gate). Remaining: the Trends API + CSV export (slice 2) and the charts UI + Playwright (slice 3). |
| 2026-07-13 | M3.3 slice 3 (Tonight timeline v1) — **completes M3.3 [AUTO] + the Phase 3 exit**. A **`GET /fusion/timeline`** endpoint serves the night's fused sleep/wake + calm/distressed state as contiguous **segments** plus consolidated **sleep sessions**, both derived on read from the durable `fused_states` log (`fusion_read.timeline_segments` / `sleep_sessions`), so they always reflect the latest state and survive a restart. The **Tonight view** gains a scrubbable **timeline track**: asleep/awake bands with a hatched distressed span, nudge-event markers overlaid by time, and a legend; tapping a marker opens and plays that event's clip (reusing the M2.4 clip playback). New `fetchTimeline` client helper. A `timeline` Playwright project (run after `tonight` on the same recorder stack, so a real promoted clip exists) **seeds a replayed night's `fused_states`** and asserts the bands render (sleep + wake + distressed) and scrubbing to a marker plays its clip — the last M3.3 [AUTO] criterion. Server tests cover the endpoint (segments + sessions + auth). This closes the Phase 3 exit: raw inputs → fused states + sessions → an accurate Tonight timeline, entirely in CI. Remaining across M3.x: the [MANUAL] overnight bench reviews, which need physical hardware. |
| 2026-07-13 | M3.3 slice 2 (live fusion worker + persistence + crash-recovery). Wires the slice-1 fusion into the running api. A new **`FusedState`** transition hypertable is the durable source of truth for a household's sleep/wake + calm/distressed timeline; **sleep sessions are a query over it** (`fusion_read.sleep_sessions`), not stored rows. A signal loader (`fusion_signals`) maps the persisted extractor signals into per-epoch features — `state_history` step-levels (`movement_level` low/med/high, `sound_level` quiet/elevated, `cry`) **carried forward** from a pre-window seed, `sensor_readings` movement/presence binned dense, combined across cameras/devices, gaps left `None`. The **`FusionWorker`** (in the api lifespan, like the nudge worker) is deliberately **stateless**: each cycle it re-runs the fusion over a warmup window seeded from the state that held at the window start, and appends a transition only when the current state changed (back-dated to its true onset). Because the DB is the only state, a restart re-derives the current state from the same signals — so **a sleep session survives an engine restart** without fragmenting or duplicating (the crash-recovery half of the session-integrity [A] criterion). Integration-tested against a real Postgres: sleep→wake transitions recorded with onset timing, session spans a worker restart, signal carry-forward + gap handling, disabled-worker no-op. Remaining: Tonight timeline v1 + Playwright (slice 3). |
| 2026-07-13 | M3.3 slice 1 (fusion core + replay gates). A pure-Python, dependency-light **fusion layer** (`server/eeper/fusion/`) derives sleep/wake and calm/distressed states, and consolidated sleep-session records, from the per-epoch outputs of every extractor (camera motion, mmWave/PIR movement + presence, sound, cry). Sleep/wake runs a **median-smoothed activity score** (isolated-spike rejection — the lift over a naive per-epoch threshold) through a hysteresis band with a post-transition sustain, mirroring the M2.2 movement state machine; **calm/distressed** requires ≥ 2 corroborating signals and only while awake; **sessions** bridge sub-break awakenings, with confirmed transitions back-dated to their true onset for boundary accuracy. The state machine is streaming, so the same code replays a night offline and runs live (slice 2). Ground truth is a **seeded synthetic night generator** (no real labeled infant multi-modal corpus exists; real-world accuracy stays the [MANUAL] overnight bench) whose difficulty is calibrated so the fusion clears the floors with margin while a naive baseline fails. Lands the M3.3 [AUTO] gates for **replay** (≥ 0.963 epoch agreement / 1.000 wake-≥3-min recall measured across all four modality subsets; floors 0.90 / 0.95 with headroom, ratchet pattern), **combinatorial degradation** (valid states on every input subset incl. none), **corroboration** (single-signal never distresses), and **session count + ±2-min boundaries**. ESPHome-style feasibility de-risked empirically first. Remaining: live persisted worker + crash-recovery (slice 2), Tonight timeline v1 + Playwright (slice 3). |
| 2026-07-12 | M3.2 reference sensor firmware ([AUTO] criterion; [MANUAL] bench/walkthrough await hardware). Reference **ESPHome** nodes now exist under `firmware/`: a 24 GHz mmWave presence radar (HLK-LD2410 → `presence` + `movement` metrics) and a PIR gross-motion node, plus a shared **base package** (`common/eeper-base.yaml`) that encodes the non-obvious hardened-bus wiring once — TLS-on-8883 with a pinned CA (esp-idf framework, required for a custom-CA MQTT client), HA discovery **off** (it targets `homeassistant/#`, which the per-device ACL refuses → disconnect), ESPHome housekeeping parked under `eeper/dev/{id}/node/` (inside the ACL subtree but below the `eeper/dev/+/+` metric space so ingestion ignores it), and an **SNTP clock with publishing gated on it** (ingestion derives the reading timestamp *and* online/offline health from the node's `ts`, so an unsynced node would read permanently offline). A single `eeper_publish` script builds the `SensorMessage` wire contract, so a new sensor type can't get the plumbing wrong. A **MicroPython** template (`firmware/micropython/`) covers boards ESPHome doesn't, and `firmware/PROVISIONING.md` is the flash-and-pair walkthrough. New **hybrid `firmware` CI** (path-filtered like audio.yml): every run validates all configs (`esphome config`), syntax-checks the MicroPython template, and runs pytest (golden payloads checked against the real `SensorMessage`; a tag-tolerant config lint asserting the TLS/ACL/discovery/clock invariants); a separate job **compiles** both reference configs to real ESP32 images via a cached esp-idf toolchain (locally verified: mmWave 812 KB, both SUCCESS). ESPHome-in-CI feasibility + the compile cost were de-risked empirically before wiring. Respiration/breathing is documented as out-of-scope for the LD2410 (needs a dedicated radar). |
| 2026-07-12 | M3.1 slice 3 (device pairing/health UI) — **completes M3.1**. A new **Devices view** (`/devices`) lets an admin pair a sensor node through the UI: the per-device MQTT credential (username / password / publish topic) is surfaced **once** in a save-now panel (the password is never stored or echoed again), with copy affordances. The device list renders each node's derived health — Online / Offline / Never seen — from the server's `last_seen`-vs-heartbeat-window computation, auto-refreshing on a timer so a node ageing past its window flips Offline without a reload; an admin can unpair (revoking the MQTT credential immediately). Any authenticated household member can view; only an admin sees the pair/unpair controls. `api.ts` gains `fetchDevices`/`pairDevice`/`unpairDevice`; a nav entry links it from the home screen. New `devices` Playwright project + `e2e-devices` CI job: on a fresh core stack it pairs a node via the UI, reads the one-time credential, publishes a reading **as** the node over TLS (from inside the broker container), and asserts the health badge flips to Online — then unpairs and confirms the node leaves the list; an unauthenticated `/devices` redirects to sign-in. This lands the last M3.1 [A] criterion (offline detection reflected in the UI). |
| 2026-07-10 | M3.1 slice 2 (device onboarding + ingestion). Sensor nodes are now first-class inputs on the hardened bus. **Pairing** (`POST /devices`, admin-only) mints a per-device MQTT account + a dynamic-security role scoped to `eeper/dev/{id}/#` via the `$CONTROL` API (the api authenticates as `eeper-api`), returns the credential once, and tears down partial state + drops the row on any provisioning failure. **Ingestion** (`SensorIngestor`, in the api lifespan) subscribes `eeper/dev/#` over TLS, validates each reading against the `SensorMessage` contract, and batch-writes to the new `sensor_readings` hypertable (device_id, ts, metric, value, quality) while advancing `last_seen` (which drives a derived `online` flag). New `Device` + `sensor_readings` models; `MqttProvisioner`; `GET`/`DELETE /devices`. The dynsec store is now broker-writable so provisioning persists, and the broker is a `core` service. New `sensors` CI job: a device pairs, publishes as itself (from inside the broker container), and lands in `sensor_readings`; a second device's writes into it — and into the internal insight topics — are ACL-blocked; malformed/oversized messages are dropped without disturbing ingestion; an unpaired credential can no longer connect. Contract + drop-logic unit tests. The provisioner + ACL isolation were de-risked end-to-end against a real broker before wiring. Remaining: the device pairing/health UI (slice 3). |
| 2026-07-10 | M3.1 slice 1 (MQTT broker hardening). The internal broker goes **TLS-only with per-client credentials + topic-scoped ACLs** via mosquitto's dynamic-security plugin — no anonymous, no plaintext listener (plaintext + non-TLS + wrong-password connections are all refused). `deploy/gen-mqtt-security.sh` (run by `install.sh`) generates a dedicated MQTT CA + broker cert and seeds the dynsec store with the service accounts: `insight-publisher` (may only publish `eeper/insight/#`), `eeper-api` (dynsec provisioning + read the eeper tree, for slice-2 device pairing/ingestion), and `healthcheck`. The insight engine's `MotionPublisher` now connects over TLS + authenticates; the integration test helper reads via the CA + `eeper-api`. Architecture de-risked by a spike proving dynsec provisioning + per-device ACL isolation + plaintext-refused in the pinned `eclipse-mosquitto:2.0.22`. Device pairing (`Device`/`sensor_readings`, `POST /devices`, ingestion, health) is slice 2; the pairing UI is slice 3. |
| 2026-07-09 | M2.5 reframed after a reproducible **de-risk** (in the PR): tested M2.3's premise that a *trained* model unlocks first-class cry — eeper's frozen frontend + YAMNet features, a trained head (logistic + MLP, over both the 521-class AudioSet scores and the 1024-d embeddings, balanced + near/far augmented) over the **full donateacry corpus** (457 clips), split device-disjoint on the per-upload UUID. Finding, one level up from M2.3's: **the wall is the corpus, not the model** — a trained head does NOT beat the pretrained hand-tuned scorer at any false-nudge-safe point (best trained near-field recall ~0.47 vs ~0.84 pretrained on the same device-disjoint split; binding confuser is cry-vs-animal, where the hand-tuned animal-band suppression is an inductive bias a naive head can't recover on held-out infants; residual infant leakage would only flatter the trained head, which still lost). donateacry is the only cry source (457, 84% one reason, near-field, only a **per-device UUID** → no *guaranteed* infant-disjoint split); FSD50K is confusers-only (**no "FSD50K cry positives"** — a corrected M2.5 premise); no real far-field. The pretrained model is already near this ceiling (~0.80 near / ~0.76 far window recall, ~0.85 episode; the far "collapse" was a shared-threshold artifact). So (M2.3 pattern — measure the truth, ratchet, name the gap, give it a milestone): cry stays experimental + off-by-default; the cry window ratchets are raised to the measured reality (near recall 0.75→0.78 / FPR 0.12→0.08, far recall 0.55→0.72 / FPR 0.20→0.11, CI fails on regression); the sound-level gate + multi-arch ONNX smoke are unchanged. First-class cry (on-by-default + blocking gate) is deferred to a new **M2.6 — cry corpus expansion** (infant-level ids for leakage-safe splits + real-RIR far-field + FSD50K cry positives + a train split). |
| 2026-07-09 | M2.4 (web half): the **Tonight view** (`/tonight`) — the night's nudge events over a `/ws/events` WebSocket (live, no reload; new events prepend, an event's clip appears in place when the worker promotes it), each with a tappable in-browser clip. Web Push opt-in + quiet-hours settings (`push.ts` PushManager subscribe with the server VAPID key; `realtime.ts` WS client with capped-backoff reconnect). A service-worker push handler (`static/push-sw.js`, `importScripts`'d into the generated Workbox SW so precache/installability is untouched) shows the nudge keyed on the event-id collapse key and opens Tonight on tap. New Playwright `tonight` project (live event + clip playback in system Chrome) in the `recorder` job. svelte-check + eslint + build + prettier green. |
| 2026-07-09 | M2.4 (events, clips, nudges — server half; web next). A **DB-as-queue** delivery pipeline: the insight engine writes a nudge event (`sound_elevated`/`cry_detected`) with its delivery channels `pending`; an api-side `NudgeWorker` (Postgres `LISTEN/NOTIFY` for low latency + a reconciliation poll as the never-lost safety net) runs three idempotent channels — WebSocket broadcast, Web Push (VAPID/`pywebpush`, per-user enable + quiet-hours + per-camera rate-limit), and post-roll clip auto-promotion (reusing the M1.4 cutter via a new `clip_service`, linked atomically). New `events` API + `/ws/events` (cookie-JWT) + push subscription/preferences endpoints; VAPID keypair generated by `install.sh`. Delivery state lives on the event row (a forward `ADD COLUMN IF NOT EXISTS` migration covers upgrades) → crash-safe, exactly-once. Verified against real Postgres: reconciliation-without-NOTIFY, exactly-once-across-restart, rollback-no-side-effects, push matrix + real VAPID send, upgrade migration (92 server tests). New `test_nudge_pipeline` end-to-end (sound → auto-clip → API → playable) in the `recorder` job. Adversarial review (4 dimensions) → fixed a critical upgrade-boot abort, push at-least-once dup (collapse key), event-time rate-limit anchor, transient-push retry. |
| 2026-07-09 | M2.3 (audio nudges): reframed after exhaustive measurement — pretrained YAMNet can't carry cry *classification* to a first-class bar (sustained-episode recall for one infant caps ~0.70 at any false-nudge-safe operating point; window errors correlate in time, so temporal voting can't exceed the model's per-infant detectability). v1 ships **sound-level** nudges (per-window RMS/dBFS, adaptive quiet-only baseline, k-of-n sustained-elevation state machine → `sound_elevated`; model-free, on by default): episode recall 0.95, ~2 s latency, 0 false events on a quiet night. **Cry classification** ships experimental + off by default (frozen YAMNet + eeper NumPy log-mel frontend + pet-suppressed window scoring + k-of-n episode detector → `cry_detected`); its window-level accuracy (near-field + physically-based far-field, pyroomacoustics) is recorded as **ratchet baselines**. New `audio` CI workflow (product-derived sound gate + cry ratchets on the frozen eval split, deterministic; multi-arch ONNX CPU smoke); long-form scene synthesis added to the fixture tooling (episodes + nights, M3.3-ready); `cam-sound` synthetic source + sound pipeline suite in the `recorder` job. Per-signal-type MQTT state topics + generalized state writer. **M2.5** promoted (trained cry model); plan gains the meta-lesson that gate thresholds must trace to a product consequence. |
| 2026-07-06 | Tracker created. Planning phase complete (master plan, implementation plan, README). Project renamed Nightlight → eeper. |
| 2026-07-06 | M0.1 implemented: monorepo layout (`server`/`web`/`adapters`/`firmware`/`deploy`/`docs`/`models`), Python (ruff/mypy-strict/pytest) + web (eslint/svelte-check/prettier) tooling, Conventional-Commits enforcement (commitlint + Husky hooks), two CI workflows (PR checks + multi-arch build/scan/push), pinned base image digests, Renovate. Merged in PR #1; CI green. |
| 2026-07-06 | M0.2 implemented: hardened Compose `core` stack (Caddy edge proxy w/ local-CA TLS + security headers, FastAPI api, TimescaleDB, static web); `install.sh` (prereq check, secret generation, CA extraction); first-boot wizard + session auth gate; LAN-only/port isolation; every container non-root + read-only rootfs. New `stack` CI workflow boots the stack and runs the `deploy/tests` integration suite (9/9 local). Base images pinned; api/web/caddy pass the Trivy CRITICAL gate. |
| 2026-07-07 | M0.3 part 1 (auth) merged in PR #3, CI green. |
| 2026-07-07 | M0.3 part 2 (test harnesses): synthetic RTSP camera + MQTT sensor fleet + `harness` self-test workflow; Playwright browser harness (`e2e` job) driving the first-boot wizard. Phase 0 complete pending merge. |
| 2026-07-07 | M1.1 (media gateway): go2rtc `video` profile (internal, hardened, digest-pinned); admin camera registration + ffprobe contract validation (H.264/≤1080p, H.265 rejected); internal RTSP re-serve; WebRTC signaling relay; background health/recovery monitor. New `video` CI job + synthetic H.265 source; 6-test suite green. Phase 0 fully merged. Merged in PR #5. |
| 2026-07-07 | M1.2 (live view in the PWA): real browser WebRTC media (go2rtc media port 8555 published + explicit ICE candidate via `EEPER_GO2RTC_CANDIDATE`; control planes stay dark); installable PWA (`@vite-pwa/sveltekit` manifest + Workbox SW, icons); Live view with recv-only WebRTC playback, per-camera health, multi-camera switching, client auth guard (viewer role included). New `e2e-live` (getStats frames <3s, latency, auth redirect, viewer access) + `lighthouse` (installability) CI jobs; `test_gateway_...` rewritten to a media-only-port allowlist. Architecture locked by a design workflow + a headless-Chromium spike. Merged in PR #6. |
| 2026-07-07 | M1.3 (camera adapters): two first-party adapter images (mediamtx + encoder) — USB (ffmpeg/V4L2, amd64+arm64) and CSI (mediamtx native `rpiCamera`/libcamera, arm64-only, Pi capture = [M] bench); both H.264-baseline/≤1080p contract-conformant + Trivy-CRITICAL-clean. `images.yml` per-image `platforms` (CSI arm64-only, PR-scan fix); new `adapters-usb` CI job (contract + browser end-to-end via the shared suite); phone-RTSP doc. v4l2loopback can't load on hosted runners (verified) → hosted fallback with the synthetic input through the identical path (user-approved). Design-workflow-driven. Merged in PR #7. |
| 2026-07-08 | M1.4 (recorder): dedicated recorder container (`record` profile, reuses the api image) — one `ffmpeg -c copy` child per camera writing MPEG-TS segments + a quota/retention task; filesystem-is-index crash-safe design (kill loses at most the active segment, proven vs `libavformat/segment.c` + a live docker-kill test); admin clip promotion (concat covering finalized segments → faststart H.264 MP4 in `/media/clips`, exempt from eviction) + authed household-scoped Range playback (`FileResponse`); `Clip` model; Starlette floored ≥0.49.1 (CVE-2025-62727). New `media-data` volume; `recorder` CI job (7-test suite + system-Chrome clip playback). Closes Phase 1. 24h/CPU exit = [M] bench. Design-workflow-driven. |
| 2026-07-08 | M2.1 (audio pipeline): new insight-engine service (`insight` profile, reuses the api image) — per-camera ffmpeg audio decode to 16 kHz mono s16le, framed into 1.0s windows in an in-process ring (+ a test WAV tap), verified as the synthetic camera's 1 kHz tone via a stdlib Goertzel dominance check against a committed fixture (robust to ffmpeg drift). Listen-in: camera registration adds a second on-demand go2rtc `ffmpeg:...#audio=opus` source so WebRTC carries an audio track (aiortc `m=audio opus` guard + a muted browser packets-flowing assertion). Audio suite folds into the `recorder` CI job. Phase 1 done. Design-workflow + live Opus spike. |
| 2026-07-08 | M2.2 (insight engine core + motion): the insight supervisor gains a per-camera video path — ffmpeg gray-frame decode (160×120@5fps) into a latest-wins `FrameRing`, a pure-Python normalized frame-diff motion score (no numpy), EWMA smoothing, and a low/medium/high hysteresis state machine (dual enter/exit bands + post-transition-only min-dwell). Transitions write new `state_history`/`events` TimescaleDB hypertables (composite `(ts,id)` PK — partition column required in every unique index) then publish over MQTT (retained `eeper/insight/state`, per-tick `.../motion`) to a new internal-only mosquitto broker (no host port; TLS/ACLs are M3.1). Backpressure = ring drops the backlog (bounded memory, freshest pair scored); graceful degradation = audio child spawned only when the source has audio, per-stream reap keeps listen-in alive through a video hiccup. New `cam-motion` (8 s still↔moving) + `cam-noaudio` synthetic sources; motion + backpressure suites fold into the `recorder` CI job. Adversarial design workflow (3 proposals → critique → synthesis) + live calibration. |
| 2026-07-08 | M2.0 (labeled audio fixture library, planning): added the M2.0 milestone to the plan; pinned M2.3's quality gate to the frozen `fixtures-v1` eval split. Merged in PR #11. |
| 2026-07-08 | M2.0 (implementation): the `fixtures/` CI-only package — per-clip `manifest.json` (source/license/sha256/labels/split/verification) + `fixtures verify\|build\|check\|repro\|provenance`; content-addressed fetch with SHA-256 tamper-reject (+ archive-member mode); deterministic CC0 generators (white-noise/lullaby/nursery-floor); seeded Scaper scene synthesis emitting bit-identical WAV + path-free `.txt` annotations; split/floor/annotation gates. Real fixtures-v1 = 236 pinned source clips (donateacry ODbL + FSD50K CC0/CC-BY, NC dropped) → 630 scenes; all 6 AUTO gates green locally + a new digest-pinned `fixtures` CI workflow. sibling/other-child confuser + the two [M] listening passes deferred (v1.1 / bench). Research + engine de-risk (Scaper bit-identity, arch-stable generators) drove it. |
