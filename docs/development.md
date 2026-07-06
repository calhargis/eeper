# Development setup

## Prerequisites

- **Docker** + Compose v2 (for images and, later, the stack).
- **Node ≥ 20** (web). CI builds with Node 22 LTS.
- **Python ≥ 3.12** (server). Server images will pin `python:3.12-slim`.

## One-time setup

```bash
make setup      # installs root tooling, web deps, and server dev deps
```

`make setup` runs `npm ci` at the repo root, which installs the Husky git hooks
via the `prepare` script. After that:

- every commit message is checked against Conventional Commits (`commit-msg`);
- staged files are formatted with Prettier (`pre-commit`).

## The check workflow

Run the same checks CI runs before pushing:

```bash
make lint        # ruff + eslint + prettier --check
make typecheck   # mypy (strict) + svelte-check
make test        # server unit tests
make format      # auto-fix formatting (ruff + prettier)
```

## Building images locally

```bash
make build-images   # builds every service Dockerfile for your local arch
```

CI builds the same images multi-arch (amd64 + arm64); see [ci.md](./ci.md).
