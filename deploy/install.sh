#!/usr/bin/env bash
#
# eeper installer. Checks prerequisites, generates secrets (no defaults), brings
# up the core stack, and extracts the local CA certificate for you to trust on
# your devices. Safe to re-run: existing secrets are preserved.
#
# Non-interactive by design (CI runs it unattended).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CA_OUT="$SCRIPT_DIR/eeper-local-ca.crt"
COMPOSE=(docker compose --profile core)

cd "$SCRIPT_DIR"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; }

# ─── prerequisites ─────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || { err "docker is not installed"; exit 1; }
docker compose version >/dev/null 2>&1 || { err "docker compose v2 is required"; exit 1; }
command -v openssl >/dev/null 2>&1 || { err "openssl is required to generate secrets"; exit 1; }

# ─── secrets ───────────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  log "Generating secrets into deploy/.env (no default credentials)"
  # Generate into checked variables first: a failed openssl aborts here (set -e)
  # instead of silently writing an empty secret that a later re-run would keep.
  postgres_password="$(openssl rand -hex 24)"
  secret_key="$(openssl rand -hex 32)"
  [ "${#postgres_password}" -eq 48 ] || { err "failed to generate POSTGRES_PASSWORD"; exit 1; }
  [ "${#secret_key}" -eq 64 ] || { err "failed to generate EEPER_SECRET_KEY"; exit 1; }
  # Write atomically (temp file + mv, same dir) so an interruption can't leave a
  # partial .env that the re-run guard below would then preserve.
  tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp_env"
  cat > "$tmp_env" <<EOF
EEPER_DOMAIN=${EEPER_DOMAIN:-localhost}
EEPER_BIND_ADDR=${EEPER_BIND_ADDR:-0.0.0.0}
EEPER_HTTP_PORT=${EEPER_HTTP_PORT:-80}
EEPER_HTTPS_PORT=${EEPER_HTTPS_PORT:-443}
POSTGRES_PASSWORD=$postgres_password
EEPER_SECRET_KEY=$secret_key
EOF
  mv "$tmp_env" "$ENV_FILE"
else
  log "deploy/.env already exists — keeping existing secrets"
fi

get_env() { grep "^$1=" "$ENV_FILE" | cut -d= -f2- | head -1; }
DOMAIN="$(get_env EEPER_DOMAIN)"
HTTPS_PORT="$(get_env EEPER_HTTPS_PORT)"

# ─── build & start ─────────────────────────────────────────────────────────
log "Building images and starting the core stack"
"${COMPOSE[@]}" up -d --build

# ─── wait for the edge to serve ────────────────────────────────────────────
log "Waiting for the stack to become healthy"
ready=false
for _ in $(seq 1 60); do
  # Use --resolve so the request carries the correct SNI/Host (the local CA cert
  # is issued for EEPER_DOMAIN, not the loopback IP) while connecting locally.
  if curl -ksSf --resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1" \
      "https://${DOMAIN}:${HTTPS_PORT}/api/v1/health" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if [ "$ready" != true ]; then
  err "stack did not become healthy in time; check: (cd deploy && docker compose --profile core logs)"
  exit 1
fi

# ─── extract the local CA ──────────────────────────────────────────────────
log "Extracting the local CA certificate"
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T caddy cat /data/caddy/pki/authorities/local/root.crt \
      > "$CA_OUT" 2>/dev/null && [ -s "$CA_OUT" ]; then
    break
  fi
  sleep 2
done

echo
if [ -s "$CA_OUT" ]; then
  log "Local CA written to: $CA_OUT"
  echo "    Trust this certificate on each device that will view the monitor."
  echo "    Guide: docs/testing/m0.2-ca-trust.md"
else
  err "could not extract the local CA yet; retrieve it later with:"
  echo "    (cd deploy && docker compose --profile core exec caddy cat /data/caddy/pki/authorities/local/root.crt)"
fi

echo
log "eeper is running. Open the first-boot wizard at:"
echo "    https://${DOMAIN}:${HTTPS_PORT}/"
echo "    (create your admin account — there are no default credentials)"
