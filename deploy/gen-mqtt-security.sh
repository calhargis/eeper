#!/usr/bin/env bash
# Generate the hardened MQTT broker's TLS material + dynamic-security seed (M3.1).
#
# The broker is TLS-only with per-client credentials and topic-scoped ACLs via
# mosquitto's dynamic-security plugin. This script generates, once (idempotent):
#   - a dedicated MQTT CA + a broker server certificate signed by it (devices trust
#     the CA to verify the broker; the CA is separate from Caddy's HTTPS CA),
#   - a dynamic-security.json seeded with the service accounts the stack needs:
#       admin              — dynsec control (break-glass / bootstrap)
#       eeper-api          — dynsec control (device provisioning) + subscribe eeper/dev/#
#       insight-publisher  — publish eeper/insight/#  (the insight engine)
#       healthcheck        — publish healthcheck      (the broker's docker healthcheck)
# Per-DEVICE accounts are minted later, at pair time, by eeper-api (M3.1 slice 2).
#
# It prints the generated service passwords as KEY=VALUE lines on stdout for the
# caller (install.sh) to append to .env; all diagnostics go to stderr. Re-running is
# a no-op once the seed exists, so credentials are never rotated out from under a
# running deployment.
set -euo pipefail

OUT_DIR="${1:?usage: gen-mqtt-security.sh <mosquitto-dir> [extra-SANs]}"
EXTRA_SANS="${2:-}" # comma-separated, e.g. "DNS:eeper.local,IP:192.168.1.10"
MOSQ='eclipse-mosquitto:2.0.22@sha256:212f89e1eaeb2c322d6441b64396e3346026674db8fa9c27beac293405c32b3c'

CERTS="$OUT_DIR/certs"
SEC="$OUT_DIR/security"
DS="$SEC/dynamic-security.json"
mkdir -p "$CERTS" "$SEC"

log() { printf '\033[36m==>\033[0m %s\n' "$*" >&2; }

if [ -f "$DS" ] && [ -f "$CERTS/mqtt-ca.crt" ] && [ -f "$CERTS/broker.crt" ]; then
  log "MQTT security already generated in $OUT_DIR — leaving it untouched"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || { echo "error: openssl required" >&2; exit 1; }

log "Generating the MQTT CA + broker certificate"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$CERTS/mqtt-ca.key" -out "$CERTS/mqtt-ca.crt" \
  -subj "/CN=eeper MQTT CA" 2>/dev/null
openssl req -newkey rsa:2048 -nodes \
  -keyout "$CERTS/broker.key" -out "$CERTS/broker.csr" \
  -subj "/CN=eeper-mqtt-broker" 2>/dev/null
san="DNS:mqtt,DNS:localhost,IP:127.0.0.1"
[ -n "$EXTRA_SANS" ] && san="$san,$EXTRA_SANS"
printf 'subjectAltName=%s\n' "$san" > "$CERTS/broker.ext"
openssl x509 -req -in "$CERTS/broker.csr" -days 3650 \
  -CA "$CERTS/mqtt-ca.crt" -CAkey "$CERTS/mqtt-ca.key" -CAcreateserial \
  -out "$CERTS/broker.crt" -extfile "$CERTS/broker.ext" 2>/dev/null
rm -f "$CERTS/broker.csr" "$CERTS/broker.ext" "$CERTS/mqtt-ca.srl"
# The broker runs as uid 1883; make the key readable to it, world-readable for the CA/cert.
chmod 644 "$CERTS"/*.crt
chmod 644 "$CERTS"/*.key

admin_pw="$(openssl rand -hex 24)"
api_pw="$(openssl rand -hex 24)"
insight_pw="$(openssl rand -hex 24)"
hc_pw="$(openssl rand -hex 24)"

log "Seeding the dynamic-security store (admin + service accounts)"
# Run the helper containers as the invoking host user (not root) so the files they
# write into the bind mount are host-owned — otherwise the chmod below fails on native
# Linux, where a root-in-container write lands as a root-owned file on the host.
runas="$(id -u):$(id -g)"
# dynsec init writes the admin client; its password prompt reads stdin under `docker -i`.
printf '%s\n%s\n' "$admin_pw" "$admin_pw" \
  | docker run --rm -i --user "$runas" -v "$SEC:/sec" "$MOSQ" \
      mosquitto_ctrl dynsec init /sec/dynamic-security.json admin >/dev/null

# Add the service accounts via a throwaway broker (the plugin writes correct JSON;
# hand-templating the PBKDF2 fields is error-prone). Plaintext, container-local only.
cat > "$SEC/bootstrap.conf" <<EOF
listener 1888 127.0.0.1
allow_anonymous false
plugin /usr/lib/mosquitto_dynamic_security.so
plugin_opt_config_file /sec/dynamic-security.json
persistence false
log_dest none
EOF
docker rm -f eeper-mqtt-bootstrap >/dev/null 2>&1 || true
docker run -d --name eeper-mqtt-bootstrap --user "$runas" -v "$SEC:/sec" "$MOSQ" \
  mosquitto -c /sec/bootstrap.conf >/dev/null
# Wait for the bootstrap broker to accept connections.
for _ in $(seq 1 20); do
  if docker exec eeper-mqtt-bootstrap mosquitto_pub -h 127.0.0.1 -p 1888 \
       -u admin -P "$admin_pw" -t '$CONTROL/ignore' -m x >/dev/null 2>&1; then break; fi
  sleep 0.3
done
ctl() { docker exec eeper-mqtt-bootstrap mosquitto_ctrl -h 127.0.0.1 -p 1888 -u admin -P "$admin_pw" dynsec "$@" >/dev/null 2>&1; }

# eeper-api: the trusted internal account — device provisioning (admin/$CONTROL) +
# read of the whole eeper tree (ingests eeper/dev/#; also lets integrations/tests read
# the insight event topics). Devices, by contrast, are locked to their own subtree.
ctl createClient eeper-api -p "$api_pw"
ctl createRole eeper-api
ctl addRoleACL eeper-api subscribePattern 'eeper/#' allow
ctl addClientRole eeper-api eeper-api
ctl addClientRole eeper-api admin
# insight-publisher: the internal insight engine's event stream only.
ctl createClient insight-publisher -p "$insight_pw"
ctl createRole insight-publisher
ctl addRoleACL insight-publisher publishClientSend 'eeper/insight/#' allow
ctl addClientRole insight-publisher insight-publisher
# healthcheck: publish to a single liveness topic, nothing else.
ctl createClient healthcheck -p "$hc_pw"
ctl createRole healthcheck
ctl addRoleACL healthcheck publishClientSend 'healthcheck' allow
ctl addClientRole healthcheck healthcheck

docker rm -f eeper-mqtt-bootstrap >/dev/null 2>&1 || true
rm -f "$SEC/bootstrap.conf"
# World read+write: the broker (uid 1883, neither owner nor group) both reads this and
# PERSISTS device provisioning back to it at pair time, so it must be broker-writable.
# It holds only PBKDF2 password hashes, and the host is already the trust boundary —
# the same posture as the certs above.
chmod 666 "$DS"

log "MQTT security generated"
cat <<EOF
EEPER_MQTT_ADMIN_PASSWORD=$admin_pw
EEPER_MQTT_API_PASSWORD=$api_pw
EEPER_MQTT_INSIGHT_PASSWORD=$insight_pw
EEPER_MQTT_HC_PASSWORD=$hc_pw
EOF
