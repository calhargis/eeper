# Continuous Integration

Four workflows live in `.github/workflows/`.

## `ci.yml` — checks on every PR

Runs on `pull_request` and on `push` to `main`. Fails the build on any violation.

| Job          | What it checks                                                                                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `commitlint` | Every commit message follows Conventional Commits                                                                                                                    |
| `python`     | `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` on `server/` (the auth-matrix tests spin up Postgres via testcontainers, so this job needs Docker) |
| `web`        | `eslint` + `svelte-check` (TypeScript) on `web/`                                                                                                                     |
| `format`     | `prettier --check` across the repo                                                                                                                                   |

## `stack.yml` — core stack integration

Brings up the `core` stack with `deploy/install.sh` on a clean runner and runs
the core integration suite (`deploy/tests/test_stack.py`), asserting the M0.2
criteria end to end: no default credentials, HTTP→HTTPS + local-CA TLS + security
headers, only Caddy publishes ports, 401 before the first-boot wizard, and every
container non-root on a read-only rootfs. Tears the stack down afterwards. (The
media-gateway suite `deploy/tests/video/` runs in the separate `video` job below,
which brings up the extra `video` profile it needs.)

Its `e2e` job is the **browser harness**: it brings up a fresh core stack and
drives the first-boot wizard with Playwright (create admin → sign out → sign in).
Its `video` job (M1.1) brings up the core + `video` profile (go2rtc) plus a
synthetic camera, then asserts the media-gateway criteria: registration makes the
stream available via internal RTSP + a WebRTC signaling answer, an H.265 source is
rejected, killing/restarting the camera flips health offline→online, and only the
go2rtc media port is published (its signaling + RTSP control planes stay dark).

Its `e2e-live` job (M1.2) brings up the core + `video` stack, provisions an admin,
a viewer, and a registered camera, then drives the PWA **Live view** with
Playwright: WebRTC playback decodes frames within 3 s (`getStats().framesDecoded`),
steady-state playout latency stays in the LAN budget, an unauthenticated `/live`
redirects to sign-in, and a viewer-role user can watch. Its `lighthouse` job runs
Lighthouse CI against the sign-in shell (with the local CA trusted in the NSS DB,
so it's a genuine secure context) and asserts PWA installability, on a throttled
mobile profile.

Its `adapters-usb` job (M1.3) brings up the core + `video` stack **plus the USB
adapter** and asserts it passes the same contract-validation suite as native
cameras: the adapter's stream registers (H.264/≤1080p contract), re-serves H.264,
answers WebRTC, is hardened, and plays end-to-end in the browser harness. The
adapter runs a **synthetic `lavfi` input through the identical encode + serve path
a real webcam takes** — hosted GitHub runners can't load the `v4l2loopback` kernel
module (kernel lockdown blocks `modprobe`), so the required gate exercises
everything but the literal V4L2 device open, which is the [MANUAL] bench item. A
best-effort, non-blocking step opportunistically attempts a real `v4l2loopback`
capture if a runner ever permits it.

Its `recorder` job (M1.4) brings up the core + `video` + `record` stack (the
recorder container, fast test settings) and asserts the ring-buffer criteria:
segments are written; a `SIGKILL` mid-segment loses **at most the active segment**
(the crash-safe filesystem index); clip promotion yields a **duration-matching
playable H.264 MP4** with faststart; the playback endpoint enforces auth and HTTP
Range; eviction over the byte quota keeps **promoted clips**; and the api's
Starlette is ≥ 0.49.1 (the CVE-2025-62727 Range-parser fix). A Playwright leg on
**system Chrome** (bundled Chromium can't decode H.264) decodes a promoted clip in
a real `<video>`.

## `harness.yml` — test-harness self-test

Brings up the synthetic inputs (`deploy/harness/`) — an RTSP synthetic camera and
an MQTT synthetic sensor fleet — and asserts both are reachable/publishing
(ffprobe the stream; subscribe and validate the sensor contract). Intended as a
required check for later milestones, which build on these inputs.

## `images.yml` — build, scan, push

Runs on `push` to `main` (build + scan + **push** to GHCR) and on
`pull_request` (build + scan for the runner arch, **no push**).

- **Discovery:** a first job finds every `**/Dockerfile` and emits a build
  matrix, so new services are picked up automatically as they land. Each entry
  carries a `platforms` value — `linux/amd64,linux/arm64` by default, but
  **`linux/arm64` only** for the Pi-only CSI adapter (`adapters/csi`).
- **Multi-arch:** images build for their target arch(es) via Buildx + QEMU. Base
  images are pinned to immutable digests.
- **Scan (before push):** Trivy scans the built image and **fails the build on
  CRITICAL CVEs** (`ignore-unfixed` to avoid un-actionable noise). PRs scan
  `amd64` for fast feedback; `main` scans **every architecture that gets
  pushed** before publishing, so no unscanned arch ships. An arm64-only image has
  no amd64 leg, so its arm64 build+scan runs on PRs too (no coverage hole).
- **Registry:** `ghcr.io/calhargis/eeper/<service>`, pushed only on `main`
  using the built-in `GITHUB_TOKEN` (no external secrets). Only the `build` job
  requests `packages: write`; everything else is `contents: read`.

## Pinned digests

Base image digests are pinned in each `Dockerfile` (`FROM image@sha256:…`).
[Renovate](../renovate.json) keeps them current and pins any new ones.

## [MANUAL] bench criteria

A few acceptance criteria can't run on hosted GitHub runners and are validated on
the reference bench (a Raspberry Pi / mini-PC) instead:

- **Real capture hardware** — a physical USB webcam and a Pi Camera Module 3
  through their adapters (M1.3); hosted runners can't `modprobe v4l2loopback` or
  attach a CSI camera.
- **Phase-1 exit: 24 h sustained record + live view under a CPU budget** (< 60 %
  steady-state, M1.4). A hosted job can neither run 24 h nor produce a stable CPU
  number, so a short proxy would be a false green. The budget is met **by
  construction**: the record path is `ffmpeg -c copy` (RTSP depacketize + TS mux,
  no decode/encode), clip promotion is on-demand `-c copy` concat, retention is
  `scandir` + `unlink`, and playback is a `sendfile` `FileResponse` — so
  steady-state CPU is a few percent per camera, dominated by I/O.
