# Contributing to eeper

Thanks for your interest! eeper is in early, phase-by-phase construction (see
[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)). A few things are load-bearing.

## The safety boundary is non-negotiable

eeper is a **sleep-insight and awareness tool, not a medical device.** Read
[Section 2 of the Master Plan](./MASTER_PLAN.md#2-safety--regulatory-stance).
PRs that add medical claims, clinical-alarm language, or diagnostic features
will be declined. This is not just policy — later phases add a CI copy-lint that
fails the build on clinical/alarm terms in user-facing strings.

## Commit messages: Conventional Commits

Every commit must follow [Conventional Commits](https://www.conventionalcommits.org):

```
<type>(<optional scope>): <description>

[optional body]
[optional footer]
```

Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`. Scopes usually match a monorepo area
(`server`, `web`, `deploy`, `firmware`, `adapters`, `models`, `ci`).

Examples:

- `feat(web): add live-view camera switcher`
- `fix(server): recover RTSP stream after gateway restart`
- `ci: pin base image digests`

This is enforced two ways: locally by a Husky `commit-msg` hook, and in CI by
the `commitlint` job. **CI rejects malformed messages**, so install the hooks:

```bash
npm ci        # runs `husky` via the prepare script and installs the hooks
```

## Before you push

Run the same checks CI runs:

```bash
make setup      # one-time: install root, web, and server dev deps
make lint       # ruff + eslint + prettier
make typecheck  # mypy (strict) + svelte-check
make test       # server unit tests
```

CI runs lint, type-check, and commit linting on every PR and will fail on any
violation. Images are built multi-arch (amd64 + arm64), scanned for critical
CVEs, and pushed to GHCR on merge to `main`.

## Repository layout

| Path        | What lives here                                     |
| ----------- | --------------------------------------------------- |
| `server/`   | Python services (api, insight-engine, recorder, …)  |
| `web/`      | SvelteKit PWA                                       |
| `adapters/` | Edge shim containers (ffmpeg USB, rpicam CSI)       |
| `firmware/` | ESPHome configs + MicroPython for sensor nodes      |
| `deploy/`   | Docker Compose, install script, TLS provisioning    |
| `docs/`     | Documentation, including `docs/testing/` procedures |
| `models/`   | ML model manifest + fetch tooling                   |
