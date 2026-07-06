# eeper — Progress Tracker

Tracks progress against [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md). Update this file in the same PR that completes work.

**How to use this file**
- A milestone is ✅ only when *every* criterion below it is checked.
- `[A]` = automated criterion (must be green in CI). `[M]` = manual procedure (record date + tester initials when performed, e.g. `✔ 2026-08-14 JD`).
- Statuses: ⬜ not started · 🔨 in progress · ✅ done · 🚧 blocked (add a note)

**Last updated:** 2026-07-06

---

## Overall status

| Phase | Milestones | Status |
|---|---|---|
| Planning (master plan, implementation plan, README) | — | ✅ done |
| Phase 0 — Skeleton | M0.1–M0.3 | 🔨 in progress (M0.1–M0.2 implemented) |
| Phase 1 — Video | M1.1–M1.4 | ⬜ not started |
| Phase 2 — Audio & first insights | M2.1–M2.4 | ⬜ not started |
| Phase 3 — Sensors & sleep states | M3.1–M3.3 | ⬜ not started |
| Phase 4 — Trends & pulse-ox | M4.1–M4.3 | ⬜ not started |
| Phase 5 — Hardening & release | M5.1–M5.2 | ⬜ not started |

**Currently working on:** M0.3 — auth system (part 1: JWT/refresh, TOTP, roles, api_tokens, lockout); test harnesses follow as part 2
**Blockers:** none
**Fixture library sourcing (long-lead item for M2.3/M3.3):** ⬜ not started — begin hunting cry corpora and recording synthetic nights early

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
> bootstrap; M1.2 tightens it with hashed CSP.

### M0.3 — Auth, users, test harnesses — 🔨 auth implemented (part 1); harnesses next (part 2)
- [x] [A] Full auth matrix: login, refresh rotation, revocation, TOTP, role denials
- [x] [A] Brute-force lockout with backoff
- [ ] [A] Harness self-test job (synthetic camera + sensor fleet) green and marked required (part 2)

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

**Phase 0 exit:** ⬜ stranger can `install.sh` on any Linux box → secure, empty, login-gated app

---

## Phase 1 — Video

### M1.1 — Media gateway & RTSP contract — ⬜
- [ ] [A] Registered synthetic camera live via WebRTC + internal RTSP within 5 s
- [ ] [A] Non-conformant codec (H.265 test source) rejected with actionable error
- [ ] [A] Stream auto-recovery within 15 s; health transitions offline→online
- [ ] [A] Signaling only via api relay; direct go2rtc access blocked

### M1.2 — Live view in the PWA — ⬜
- [ ] [A] Playwright: WebRTC frames flowing within 3 s of page load
- [ ] [A] Playwright: auth redirect; viewer role can view
- [ ] [A] Bench: glass-to-glass < 500 ms (burned-in timestamp comparison)
- [ ] [A] Lighthouse: installability + mobile performance budget
- [ ] [M] Physical iOS + Android: install, live view, lock/unlock, camera switching — ______
- [ ] [M] NoIR + IR illuminator usable image in dark room — ______

### M1.3 — Adapters (USB & Pi CSI) — ⬜
- [ ] [A] USB adapter (V4L2 loopback in CI) → contract-conformant stream end-to-end
- [ ] [A] Adapter images multi-arch + pass contract validation suite
- [ ] [M] Physical USB webcam + CSI Camera Module 3 stream via adapters on bench — ______
- [ ] [M] Android phone RTSP-app onboarding using only the doc — ______

### M1.4 — Recorder — ⬜
- [ ] [A] Ring buffer: segments written, quota eviction, promoted clips survive
- [ ] [A] Promoted clip playable, duration/timestamps match (ffprobe)
- [ ] [A] Playback endpoint auth-enforced, playable in browser harness
- [ ] [A] Crash mid-segment loses at most active segment; index consistent

**Phase 1 exit:** ⬜ bench sustains 24 h recording + live view < 60 % CPU [A]

---

## Phase 2 — Audio & First Insights

### M2.1 — Audio pipeline — ⬜
- [ ] [A] Known audio track arrives as 16 kHz mono PCM (fixture checksum)
- [ ] [A] Live view audio packets flowing (getStats)
- [ ] [M] Real mic intelligible, no gross A/V drift over 10 min — ______

### M2.2 — Insight engine core + motion — ⬜
- [ ] [A] Motion score ordering: still < rolling < sitting-up
- [ ] [A] Hysteresis: threshold-oscillating trace → ≤ 1 state change
- [ ] [A] Rolling fixture → movement state event on MQTT + DB row within 2 s
- [ ] [A] Backpressure: frames dropped, memory bounded, freshness < 3 s
- [ ] [A] Video-only degradation: registry matches available inputs

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
