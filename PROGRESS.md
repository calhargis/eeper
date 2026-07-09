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
| Phase 2 — Audio & first insights | M2.1–M2.4 | 🔨 in progress (M2.1 merged; M2.2 in review) |
| Phase 3 — Sensors & sleep states | M3.1–M3.3 | ⬜ not started |
| Phase 4 — Trends & pulse-ox | M4.1–M4.3 | ⬜ not started |
| Phase 5 — Hardening & release | M5.1–M5.2 | ⬜ not started |

**Currently working on:** M2.0 (labeled audio fixture library) in review; M2.3 (cry detection) infra on a branch, awaiting fixtures-v1
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
- [ ] [M] Local CA trusted on physical iOS + Android from docs alone — ______ (procedure: [docs/testing/m0.2-ca-trust.md](./docs/testing/m0.2-ca-trust.md))

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
[MANUAL] bench item ([docs/ci.md](docs/ci.md)) — no self-hosted runner; met by
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

### M2.3 — Cry detection — ⬜
- [ ] [A] Model fetch: checksum verification, tampered file refused
- [ ] [A] Quality gate: recall ≥ 0.9 on cries, FPR ≤ 0.1 on confusers
- [ ] [A] Cry fixture → `cry_detected` event end-to-end within 2 s
- [ ] [A] ONNX CPU inference smoke test on amd64 + arm64
- [ ] [M] Speaker-played cry triggers; 30 min of TV does not — ______

### M2.4 — Events, clips, nudges — ⬜
- [ ] [A] Cry event auto-promotes pre/post-roll clip, linked and playable
- [ ] [A] Playwright: event appears in Tonight v0 via WebSocket; clip plays
- [ ] [A] Web Push matrix: subscribed receives; opted-out/quiet-hours do not
- [ ] [A] Copy lint: notification templates pass clinical-terms denylist
- [ ] [M] Push arrives on physical iOS + Android, backgrounded + locked — ______

**Phase 2 exit:** ⬜ [M] speaker cry → phone nudge → playable clip on bench — ______

---

## Phase 3 — Sensors & Sleep States

### M3.1 — MQTT bus & device onboarding — ⬜
- [ ] [A] ACL matrix: cross-device publish/subscribe denied for every class
- [ ] [A] Fuzzing: malformed/oversized messages rejected without crash/slowdown
- [ ] [A] Synthetic mmWave pairs, publishes, lands in `sensor_readings` with quality
- [ ] [A] Offline detection within heartbeat window, reflected in UI
- [ ] [A] Plaintext MQTT refused

### M3.2 — Reference sensor firmware — ⬜
- [ ] [A] ESPHome configs compile in CI; payloads validate against contract schema
- [ ] [M] Physical mmWave + PIR detect person-analog at crib distance; 24 h uptime — ______
- [ ] [M] Non-author flashes and pairs a node from docs alone — ______

### M3.3 — Fusion state machine — ⬜
- [ ] [A] Replay gate: ≥ 90 % sleep/wake epoch agreement; all wakes ≥ 3 min detected
- [ ] [A] Combinatorial degradation: valid states under every input subset
- [ ] [A] Corroboration: no distress from single signal when ≥ 2 inputs live
- [ ] [A] Session integrity: count + boundaries ±2 min; survives engine restart
- [ ] [A] Playwright: Tonight timeline renders replayed night; scrub-to-clip works
- [ ] [M] Live overnight bench run reviewed against reality notes — ______

**Phase 3 exit:** ⬜ full replayed night → accurate timeline, zero intervention [A]; job runs nightly

---

## Phase 4 — Trends & Pulse-Ox

### M4.1 — Trends — ⬜
- [ ] [A] Rollups match independently computed values on seeded month
- [ ] [A] Every Trends query < 200 ms over seeded year (bench)
- [ ] [A] Compressed/uncompressed query results identical
- [ ] [A] Playwright: charts render; CSV export matches; viewer denied export

### M4.2 — Pulse-ox (optional, gated) — ⬜
- [ ] [A] Gating matrix: inert without profile + acknowledged disclaimer
- [ ] [A] Low-quality samples discarded, not stored/fused; discard rate observable
- [ ] [A] Copy lint on pulse-ox strings; accuracy caveat asserted on every view
- [ ] [A] Fusion consumes HR features only from quality-gated samples
- [ ] [M] Adult-wearer bench readings plausible; motion artifacts down-weighted — ______

### M4.3 — Operations polish — ⬜
- [ ] [A] Backup → fresh stack → restore round-trip, checksummed identical
- [ ] [A] Retention matrix evicts exactly the expected artifacts
- [ ] [A] Playwright role sweep: viewer scope correct on every route

**Phase 4 exit:** ⬜ v1.0 feature-complete; full [A] suite green; [M] checklist current

---

## Phase 5 — Hardening & Release

### M5.1 — Security review — ⬜
- [ ] [A] Zero critical CVEs; secrets scan clean; all prior security suites green
- [ ] [A] Auth fuzz corpus: tampering/replay/downgrade all rejected
- [ ] [M] Pen test of default install: no unresolved critical/high findings — ______

### M5.2 — Performance gate & docs — ⬜
- [ ] [A] Reference bench: < 60 % CPU, latency budgets met, 72 h clean run
- [ ] [A] Docs link checker + install-doc smoke test on clean VM
- [ ] [M] Two external testers cold-start on non-Pi hardware from docs — ______
- [ ] [M] Clinician/child-health review of all safety copy — ______

**v1.0 release:** ⬜ all [A] green on both architectures · ⬜ all [M] recorded for the RC · ⬜ signed images published · ⬜ release notes include safety stance verbatim

---

## Change log

| Date | Change |
|---|---|
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
