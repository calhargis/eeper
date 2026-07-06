# eeper — developer entry points. `make help` lists everything.
# These targets mirror the checks CI runs, so `make lint typecheck test` locally
# should predict a green PR.

.DEFAULT_GOAL := help
SHELL := /bin/bash

# Space-separated list of image build contexts discovered from Dockerfiles.
# Kept in sync with .github/workflows/images.yml (both use the same discovery).
PLATFORMS ?= linux/amd64,linux/arm64

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Install all dev dependencies (root tooling, web, server)
	npm ci
	cd web && npm ci
	cd server && python3 -m pip install -e ".[dev]"

# ─── Lint ──────────────────────────────────────────────────────────────────
.PHONY: lint
lint: lint-python lint-web lint-format ## Run all linters

.PHONY: lint-python
lint-python: ## ruff check on server/
	cd server && ruff check .

.PHONY: lint-web
lint-web: ## eslint on web/
	cd web && npm run lint

.PHONY: lint-format
lint-format: ## prettier --check on the repo
	npx prettier --check .

# ─── Types ─────────────────────────────────────────────────────────────────
.PHONY: typecheck
typecheck: typecheck-python typecheck-web ## Run all type checks

.PHONY: typecheck-python
typecheck-python: ## mypy (strict) on server/
	cd server && mypy .

.PHONY: typecheck-web
typecheck-web: ## svelte-check / tsc on web/
	cd web && npm run check

# ─── Tests ─────────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run server tests (auth-matrix tests use a throwaway Postgres — needs Docker)
	cd server && pytest

# ─── Format (write) ────────────────────────────────────────────────────────
.PHONY: format
format: ## Auto-format everything (ruff + prettier)
	cd server && ruff check --fix . && ruff format .
	npx prettier --write .

# ─── Images ────────────────────────────────────────────────────────────────
.PHONY: build-images
build-images: ## Build every service image for the local arch (no push)
	@for df in $$(find . -name Dockerfile -not -path './node_modules/*'); do \
		ctx=$$(dirname $$df); \
		name=$$(basename $$ctx); \
		echo "==> building $$name ($$ctx)"; \
		docker build -t eeper/$$name:dev $$ctx || exit 1; \
	done

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf web/build web/.svelte-kit .mypy_cache .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
