# Continuous Integration

Three workflows live in `.github/workflows/`.

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
the integration suite (`deploy/tests/`), asserting the M0.2 criteria end to end:
no default credentials, HTTP→HTTPS + local-CA TLS + security headers, only Caddy
publishes ports, 401 before the first-boot wizard, and every container non-root
on a read-only rootfs. Tears the stack down afterwards.

## `images.yml` — build, scan, push

Runs on `push` to `main` (build + scan + **push** to GHCR) and on
`pull_request` (build + scan for the runner arch, **no push**).

- **Discovery:** a first job finds every `**/Dockerfile` and emits a build
  matrix, so new services are picked up automatically as they land.
- **Multi-arch:** images build for `linux/amd64` and `linux/arm64` via Buildx +
  QEMU. Base images are pinned to immutable digests.
- **Scan (before push):** Trivy scans the built image and **fails the build on
  CRITICAL CVEs** (`ignore-unfixed` to avoid un-actionable noise). PRs scan
  `amd64` for fast feedback; `main` scans **every architecture that gets
  pushed** (`amd64` + `arm64`) before publishing, so no unscanned arch ships.
- **Registry:** `ghcr.io/calhargis/eeper/<service>`, pushed only on `main`
  using the built-in `GITHUB_TOKEN` (no external secrets). Only the `build` job
  requests `packages: write`; everything else is `contents: read`.

## Pinned digests

Base image digests are pinned in each `Dockerfile` (`FROM image@sha256:…`).
[Renovate](../renovate.json) keeps them current and pins any new ones.
