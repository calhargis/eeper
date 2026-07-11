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

# Best-effort host LAN IP for the WebRTC ICE candidate. Prefers an explicit
# EEPER_DOMAIN when it is already an IP; else asks the routing table (Linux);
# else falls back to loopback (host-only). Never fails under `set -e`.
detect_lan_ip() {
  local domain="${EEPER_DOMAIN:-localhost}"
  if printf '%s' "$domain" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    printf '%s' "$domain"
    return 0
  fi
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip route get 1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
  fi
  printf '%s' "${ip:-127.0.0.1}"
}

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
  # VAPID keypair for Web Push nudges (M2.4): a P-256 keypair, base64url-encoded. In a
  # SEC1 P-256 DER the 7-byte header is followed by the 32-byte private scalar; the
  # public key is the trailing 65-byte uncompressed point of the SPKI DER.
  vapid_der="$(mktemp "${ENV_FILE}.vapidXXXXXX")"
  openssl ecparam -name prime256v1 -genkey -noout -outform DER > "$vapid_der"
  vapid_private="$(dd if="$vapid_der" bs=1 skip=7 count=32 2>/dev/null | base64 | tr '+/' '-_' | tr -d '=' | tr -d '\n')"
  vapid_public="$(openssl ec -inform DER -in "$vapid_der" -pubout -outform DER 2>/dev/null | tail -c 65 | base64 | tr '+/' '-_' | tr -d '=' | tr -d '\n')"
  rm -f "$vapid_der"
  [ "${#vapid_private}" -eq 43 ] || { err "failed to generate EEPER_VAPID_PRIVATE_KEY"; exit 1; }
  [ "${#vapid_public}" -eq 87 ] || { err "failed to generate EEPER_VAPID_PUBLIC_KEY"; exit 1; }
  # Hardened MQTT broker (M3.1): generate its TLS material + the dynamic-security seed
  # and capture the per-service passwords (KEY=VALUE lines) to fold into .env below.
  log "Generating the MQTT broker TLS material + service credentials"
  mqtt_creds="$(bash "$SCRIPT_DIR/gen-mqtt-security.sh" "$SCRIPT_DIR/mosquitto")"
  printf '%s' "$mqtt_creds" | grep -q '^EEPER_MQTT_INSIGHT_PASSWORD=..' \
    || { err "failed to generate MQTT credentials"; exit 1; }
  # The host address a browser reaches go2rtc's WebRTC media port (8555) on. It is
  # published there and advertised as the ICE candidate (go2rtc excludes its own
  # Docker-bridge address, so this must be explicit). 127.0.0.1 works only on the
  # host itself; a real LAN install needs the host's LAN IP so phones can connect.
  go2rtc_candidate="${EEPER_GO2RTC_CANDIDATE:-$(detect_lan_ip)}"
  cand_domain="${EEPER_DOMAIN:-localhost}"
  # Docker binds the media port to this address, so it must be a literal IP — a
  # hostname makes `docker compose up` hard-fail with "invalid IP address".
  if ! printf '%s' "$go2rtc_candidate" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    err "EEPER_GO2RTC_CANDIDATE must be an IPv4 address, got: '$go2rtc_candidate'"
    echo "    Set it to the host's LAN IP (not a hostname) in deploy/.env." >&2
    exit 1
  fi
  # Write atomically (temp file + mv, same dir) so an interruption can't leave a
  # partial .env that the re-run guard below would then preserve.
  tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
  chmod 600 "$tmp_env"
  cat > "$tmp_env" <<EOF
EEPER_DOMAIN=${EEPER_DOMAIN:-localhost}
EEPER_BIND_ADDR=${EEPER_BIND_ADDR:-0.0.0.0}
EEPER_HTTP_PORT=${EEPER_HTTP_PORT:-80}
EEPER_HTTPS_PORT=${EEPER_HTTPS_PORT:-443}
EEPER_GO2RTC_CANDIDATE=$go2rtc_candidate
POSTGRES_PASSWORD=$postgres_password
EEPER_SECRET_KEY=$secret_key
EEPER_VAPID_PUBLIC_KEY=$vapid_public
EEPER_VAPID_PRIVATE_KEY=$vapid_private
EEPER_VAPID_SUBJECT=mailto:admin@${EEPER_DOMAIN:-localhost}
$mqtt_creds
EOF
  mv "$tmp_env" "$ENV_FILE"
  if [ "$go2rtc_candidate" = "127.0.0.1" ] && [ "$cand_domain" != "localhost" ]; then
    err "could not detect a LAN IP for WebRTC — live view will work only on this host."
    echo "    Set EEPER_GO2RTC_CANDIDATE to the host's LAN IP in deploy/.env and re-run." >&2
  fi
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
