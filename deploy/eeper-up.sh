#!/usr/bin/env bash
# eeper-up.sh — start everything eeper needs and validate each piece came up.
#
# Brings up Tailscale, then the Docker Compose stack (every service across the
# profiles below, including deployment-local override services like the camera
# adapter and thermal node), then checks each one is healthy — with a live spinner
# and a ✓/✗ per item.
#
#   Usage (on the eeper host):   sudo ./eeper-up.sh
#
# Re-execs itself with sudo when needed: Tailscale and the root-owned compose .env
# both require root. Set NO_COLOR=1 for plain output.

set -uo pipefail

# ── the compose profiles this deployment runs ───────────────────────────────
PROFILES=(core video insight)

# ── run as root ─────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then exec sudo "$0" "$@"; fi

# ── locate the compose project (this script lives in deploy/) ───────────────
cd "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)" || exit 1

# ── presentation ────────────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; B=$'\033[1m'; Z=$'\033[0m'; K=$'\033[K'
  OK='✓'; NO='✗'; SPIN='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
else
  G=''; R=''; D=''; B=''; Z=''; K=''; OK='[ok]'; NO='[!!]'; SPIN='|/-\'
fi
FAILED=0
PA=(); for p in "${PROFILES[@]}"; do PA+=(--profile "$p"); done

label_for() {
  case "$1" in
    db) echo "Database (TimescaleDB)" ;;
    mqtt) echo "MQTT broker" ;;
    api) echo "API server" ;;
    caddy) echo "Caddy (TLS + reverse proxy)" ;;
    web) echo "Web app" ;;
    go2rtc) echo "Media gateway (go2rtc)" ;;
    insight) echo "Insight engine" ;;
    recorder) echo "Recorder" ;;
    csi-adapter) echo "Camera adapter (CSI)" ;;
    usb-adapter) echo "Camera adapter (USB)" ;;
    thermal-node) echo "Thermal node (MLX90640)" ;;
    *) echo "$1" ;;
  esac
}

# Run a command with a spinner; ✓ on success, ✗ (+ the last log lines) on failure.
step() {
  local label="$1"; shift
  ("$@") >/tmp/eeper-ops.log 2>&1 &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${D}%s${Z} %s${K}" "${SPIN:i++%${#SPIN}:1}" "$label"; sleep 0.1
  done
  if wait "$pid"; then
    printf "\r  ${G}%s${Z} %s${K}\n" "$OK" "$label"
  else
    printf "\r  ${R}%s${Z} %s${K}\n" "$NO" "$label"
    sed 's/^/        /' /tmp/eeper-ops.log | tail -4
    FAILED=1; return 1
  fi
}

# Poll a condition with a spinner until it holds or times out.
wait_for() {
  local label="$1" timeout="$2"; shift 2
  local i=0 end=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$end" ]; then
      printf "\r  ${R}%s${Z} %s ${D}(timed out)${Z}${K}\n" "$NO" "$label"; FAILED=1; return 1
    fi
    printf "\r  ${D}%s${Z} %s${K}" "${SPIN:i++%${#SPIN}:1}" "$label"; sleep 0.2
  done
  printf "\r  ${G}%s${Z} %s${K}\n" "$OK" "$label"
}

# A container is up when it's running AND (healthy, or it has no healthcheck).
healthy() {
  local c="$1" s h
  s=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null) || return 1
  [ "$s" = running ] || return 1
  h=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null)
  [ "$h" = healthy ] || [ "$h" = none ]
}

printf '\n  %sBringing eeper up%s\n\n' "$B" "$Z"

# 1) Tailscale (first, so remote access is ready)
step     "Tailscale — connecting" tailscale up
wait_for "Tailscale — online" 20 tailscale status

# 2) the Docker stack
step "eeper stack — starting containers" docker compose "${PA[@]}" up -d --remove-orphans

# 3) each service healthy
for svc in $(docker compose "${PA[@]}" config --services 2>/dev/null | sort); do
  cid=$(docker compose "${PA[@]}" ps -q "$svc" 2>/dev/null)
  if [ -z "$cid" ]; then
    printf "  ${R}%s${Z} %s ${D}(not created)${Z}\n" "$NO" "$(label_for "$svc")"; FAILED=1; continue
  fi
  wait_for "$(label_for "$svc")" 120 healthy "$cid"
done

# 4) end-to-end: the edge answers (Caddy on :80 redirects to https → curl exits 0)
wait_for "Web edge reachable (Caddy)" 20 curl -fsS -o /dev/null http://localhost/

echo
if [ "$FAILED" -eq 0 ]; then
  ip=$(tailscale ip -4 2>/dev/null | head -1)
  printf '  %s%sAll systems up.%s  %s%s%s\n\n' "$G" "$B" "$Z" "$D" "${ip:+Tailscale $ip}" "$Z"
else
  printf '  %s%sSome checks failed — see the %s items above.%s\n\n' "$R" "$B" "$NO" "$Z"
  exit 1
fi
