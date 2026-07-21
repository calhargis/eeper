#!/usr/bin/env bash
# eeper-down.sh — gracefully stop everything eeper and validate each piece stopped.
#
# Tears down the Docker Compose stack (SIGTERM → grace → remove), then stops
# Tailscale — with a live spinner and a ✓ per item.
#
#   Usage (on the eeper host):   sudo ./eeper-down.sh
#
# NOTE: Tailscale is stopped LAST. If you are connected over Tailscale SSH, your
# session disconnects at that step (the shutdown still completes on the host). Run
# it from the Pi's console or a LAN connection to see every ✓. Set NO_COLOR=1 for
# plain output.

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

# A service is gone when compose no longer has a container for it.
gone() { [ -z "$(docker compose "${PA[@]}" ps -aq "$1" 2>/dev/null)" ]; }

printf '\n  %sShutting eeper down%s\n\n' "$B" "$Z"

# Snapshot the service list before teardown (config lists them regardless of state).
services=$(docker compose "${PA[@]}" config --services 2>/dev/null | sort)

# 1) stop + remove the stack (graceful SIGTERM, then remove containers; volumes kept)
step "eeper stack — stopping containers" docker compose "${PA[@]}" down --timeout 20 --remove-orphans

# 2) confirm each service is gone
for svc in $services; do
  wait_for "$(label_for "$svc") — stopped" 30 gone "$svc"
done

# 3) Tailscale LAST (this drops a Tailscale SSH session)
printf '  %sStopping Tailscale next — a Tailscale SSH session will disconnect here.%s\n' "$D" "$Z"
step     "Tailscale — disconnecting" tailscale down
wait_for "Tailscale — offline" 15 sh -c '! tailscale status >/dev/null 2>&1'

echo
if [ "$FAILED" -eq 0 ]; then
  printf '  %s%seeper is fully stopped.%s\n\n' "$G" "$B" "$Z"
else
  printf '  %s%sSome items did not stop cleanly — see the %s above.%s\n\n' "$R" "$B" "$NO" "$Z"
  exit 1
fi
