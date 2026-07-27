#!/usr/bin/env bash
# eeper-update.sh — pull the latest eeper, rebuild every image, and restart cleanly.
#
#   Usage (on the eeper host):   sudo ./eeper-update.sh
#     --no-pull    rebuild + restart the current checkout, without fetching
#     --force      rebuild even when the pull brought nothing new
#     --yes        don't ask for confirmation
#
# Why this exists rather than "down, git pull, up":
#
#   * `eeper-up.sh` does NOT build. A pull followed by an up leaves new source code running
#     on the OLD images, and the stack comes up looking healthy — the failure is silent,
#     which is the worst kind. (This really happened: a merged thermal-detector fix ran
#     nowhere for 16 hours because the images were never rebuilt.)
#   * `docker compose build` does not cover the thermal node. That image has no build
#     context in the Compose file — it is the api image plus the MLX90640 drivers — so it
#     must be rebuilt separately, and AFTER the api image it is layered on.
#
# Order matters here. Everything is built while the stack is still RUNNING, and only then is
# it restarted, so a broken build leaves a working baby monitor untouched instead of a dead
# one. There is no full `down`: `compose up -d` recreates exactly the containers whose image
# changed, which also means Tailscale is never stopped and running this over SSH is safe.
#
# It finishes by checking that every container is actually running the image that was just
# built — the one thing that makes "silent staleness" impossible rather than merely unlikely.

set -uo pipefail

PULL=1
FORCE=0
ASSUME_YES=0
for arg in "${@:-}"; do
  case "$arg" in
    "") ;;
    --no-pull) PULL=0 ;;
    --force) FORCE=1 ;;
    --yes | -y) ASSUME_YES=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ── run as root (docker + the root-owned .env) ──────────────────────────────
if [ "$(id -u)" -ne 0 ]; then exec sudo "$0" "$@"; fi

cd "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)" || exit 1
REPO=$(cd .. && pwd)
BUILD_LOG=/tmp/eeper-update.log

# ── presentation (matches eeper-up.sh / eeper-down.sh) ──────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; B=$'\033[1m'; Z=$'\033[0m'
  OK='✓'; NO='✗'
else
  G=''; R=''; Y=''; D=''; B=''; Z=''; OK='[ok]'; NO='[!!]'
fi

die() { printf '\n  %s%s %s%s\n\n' "$R" "$NO" "$1" "$Z"; exit 1; }
note() { printf '  %s%s%s %s\n' "$G" "$OK" "$Z" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$Z" "$1"; }

# git must NOT run as root against the operator's checkout: root-owned objects left in
# .git break their next ordinary `git pull` with a permission error that is baffling to
# diagnose. Run it as whoever owns the repo.
REPO_OWNER=$(stat -c %U "$REPO" 2>/dev/null || echo root)
git_() {
  if [ "$REPO_OWNER" = root ]; then git -C "$REPO" "$@"; else sudo -u "$REPO_OWNER" git -C "$REPO" "$@"; fi
}

printf '\n  %sUpdating eeper%s\n\n' "$B" "$Z"

# ── preflight ───────────────────────────────────────────────────────────────
# The persistent volumes live on the SSD. Starting with it unmounted would let Postgres
# initialise a fresh EMPTY database on the SD card underneath the mount point, which looks
# exactly like total data loss. Refuse rather than risk it. Read the paths compose will
# actually bind, not the override text — a path in a comment is not a mount.
command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is not available"

PROFILES=(core video insight record)
PA=(); for p in "${PROFILES[@]}"; do PA+=(--profile "$p"); done

COMPOSE_CONFIG=$(docker compose "${PA[@]}" config 2>/dev/null) \
  || die "\`docker compose config\` failed — fix the Compose files before updating"

# Host data paths appear in TWO shapes and both must be checked. A plain bind shows up as a
# service's `source:`, but a named volume pinned to a disk (how this deployment puts Postgres
# and the recordings on the SSD) shows up only as `driver_opts.device:` at the bottom of the
# config. An earlier version of this check grepped `source:` alone and silently found nothing
# — a safety guard that never fires is worse than none, because it reads as a pass.
external_mounted() {
  # True when some ancestor of $1 under a conventional external-storage root is a mount.
  local path="$1"
  while [ "$path" != "/" ] && [ -n "$path" ]; do
    mountpoint -q "$path" 2>/dev/null && return 0
    path=$(dirname "$path")
  done
  return 1
}

while read -r host_path; do
  [ -n "$host_path" ] || continue
  if external_mounted "$host_path"; then
    note "$host_path is on a mounted disk"
  elif [ -d "$host_path" ]; then
    # The dangerous case: the directory exists on the root filesystem where a separate disk
    # is expected, so Postgres would initialise an EMPTY database underneath the absent
    # mount. An operator who genuinely keeps data on the SD card can accept it with --yes.
    warn "$host_path exists but nothing is mounted there"
    [ "$ASSUME_YES" -eq 1 ] || die "$host_path holds deployment data but its disk is not mounted. Mount it first, or re-run with --yes if the data really does live on the root filesystem."
  else
    die "$host_path holds deployment data but does not exist. Mount the disk first."
  fi
done < <(printf '%s\n' "$COMPOSE_CONFIG" \
  | grep -oE '^[[:space:]]+(source|device): (/mnt|/media|/srv|/storage|/data)/[^[:space:]]+' \
  | awk '{print $2}' | sort -u)

git_ rev-parse HEAD >/dev/null 2>&1 || die "$REPO is not a git repository"
BEFORE=$(git_ rev-parse HEAD)

# ── pull ────────────────────────────────────────────────────────────────────
if [ "$PULL" -eq 1 ]; then
  if [ -n "$(git_ status --porcelain --untracked-files=no)" ]; then
    die "$REPO has uncommitted changes to tracked files. Commit or stash them first, or use --no-pull to rebuild what is checked out."
  fi
  # A detached HEAD is how an operator pins a rollback. `rev-parse --abbrev-ref HEAD` returns
  # the literal "HEAD" there, and `origin/HEAD` is a REAL ref (origin's default branch), so a
  # naive fast-forward would silently drag the pin back onto main — undoing the rollback and
  # rebuilding the very release it was pinned away from.
  git_ symbolic-ref -q HEAD >/dev/null \
    || die "detached HEAD at $(git_ rev-parse --short HEAD) — this looks like a pinned rollback, so refusing to move it. Use --no-pull to rebuild it as-is, or check out a branch."
  UPSTREAM=$(git_ rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) \
    || die "$(git_ rev-parse --abbrev-ref HEAD) has no upstream branch — set one, or use --no-pull."
  git_ fetch --quiet "${UPSTREAM%%/*}" || die "could not fetch from ${UPSTREAM%%/*}"
  git_ merge --ff-only "$UPSTREAM" --quiet \
    || die "cannot fast-forward onto $UPSTREAM — the local branch has diverged; resolve it by hand"
  note "pulled $(git_ rev-parse --abbrev-ref HEAD) from $UPSTREAM"
else
  warn "skipping the pull (--no-pull) — rebuilding what is checked out"
fi

AFTER=$(git_ rev-parse HEAD)
# --no-pull means "rebuild this checkout", so it must NOT be short-circuited by the
# nothing-changed check: BEFORE always equals AFTER when no pull ran.
if [ "$PULL" -eq 1 ] && [ "$BEFORE" = "$AFTER" ] && [ "$FORCE" -eq 0 ]; then
  printf '\n  %salready up to date%s (%s)\n' "$B" "$Z" "$(git_ log --oneline -1)"
  printf '  %sNothing rebuilt or restarted. Use --force to rebuild anyway.%s\n\n' "$D" "$Z"
  exit 0
fi
[ "$BEFORE" = "$AFTER" ] || printf '  %s%s%s\n' "$D" "$(git_ log --oneline "$BEFORE..$AFTER" | head -8)" "$Z"

if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; then
  printf '\n  Rebuild images and restart eeper? %s[y/N]%s ' "$D" "$Z"
  read -r reply
  case "$reply" in [yY]*) ;; *) printf '  aborted.\n\n'; exit 0 ;; esac
fi

# ── build, with the stack still up ──────────────────────────────────────────
# A build failure here must leave the running monitor alone, so nothing is stopped until
# every image is built.
printf '\n  %sBuilding (the stack keeps running)%s\n\n' "$B" "$Z"
build_failed() {
  tail -20 "$BUILD_LOG" 2>/dev/null | sed 's/^/        /'
  die "$1 — nothing was stopped, eeper is still running the previous version (full log: $BUILD_LOG)"
}
docker compose "${PA[@]}" build >"$BUILD_LOG" 2>&1 || build_failed "image build failed"
note "service images built"

# The thermal node is the api image plus the MLX90640 drivers, so it MUST be built after the
# api image and cannot be built by compose (it has no build context there). Only rebuild it
# when this deployment actually runs one.
THERMAL_IMAGE=eeper-thermal-node:dev
if printf '%s\n' "$COMPOSE_CONFIG" | grep -q "$THERMAL_IMAGE"; then
  docker build -t "$THERMAL_IMAGE" -f "$REPO/server/Dockerfile.thermal" "$REPO/server" \
    >"$BUILD_LOG" 2>&1 || build_failed "thermal node build failed"
  note "thermal node image built"
else
  printf '  %sno thermal node in this deployment — skipped%s\n' "$D" "$Z"
fi

# ── restart ─────────────────────────────────────────────────────────────────
# eeper-up.sh recreates the containers whose image changed and then validates each service.
# No `down`: Tailscale stays up, so running this over SSH is safe and downtime is one
# container recreate rather than a full stack cycle.
[ -x ./eeper-up.sh ] || die "./eeper-up.sh is missing or not executable — images are built; run it by hand"
printf '\n'
./eeper-up.sh
UP_STATUS=$?

# ── prove it actually landed ────────────────────────────────────────────────
# The whole point of this script is that nothing is left on stale code, so verify rather
# than assume: every running container must be on the image its tag now resolves to.
printf '\n  %sVerifying every service runs the image just built%s\n\n' "$B" "$Z"
STALE=0
for svc in $(docker compose "${PA[@]}" config --services 2>/dev/null | sort); do
  cid=$(docker compose "${PA[@]}" ps -q "$svc" 2>/dev/null)
  [ -n "$cid" ] || continue
  running=$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null)
  tag=$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null)
  wanted=$(docker image inspect -f '{{.Id}}' "$tag" 2>/dev/null)
  if [ -n "$wanted" ] && [ "$running" != "$wanted" ]; then
    printf "  %s%s%s %s is running an OLD image\n" "$R" "$NO" "$Z" "$svc"
    STALE=1
  fi
done
if [ "$STALE" -eq 0 ]; then
  note "every service is on its current image"
else
  die "some services did not pick up the new image — try \`docker compose ${PA[*]} up -d --force-recreate\`"
fi

echo
if [ "$UP_STATUS" -eq 0 ]; then
  printf '  %s%seeper updated to %s.%s\n\n' "$G" "$B" "$(git_ log --oneline -1)" "$Z"
else
  printf '  %s%sImages updated, but some services did not come up cleanly — see above.%s\n\n' "$R" "$B" "$Z"
  exit 1
fi
