#!/usr/bin/env bash
# eeper restore — rebuild the database + media from a backup.sh snapshot.
#
# DESTRUCTIVE: drops and recreates the `eeper` database and replaces the media volume.
# The app services are stopped first (a DROP DATABASE needs no live connections); bring
# the stack back up afterwards with ./install.sh. See docs/operations/backup-restore.md.
set -euo pipefail

cd "$(dirname "$0")"
PROJECT=eeper

DIR="${1:?usage: ./restore.sh <backup-dir>}"
[ -f "$DIR/db.dump" ] || {
  echo "missing $DIR/db.dump" >&2
  exit 1
}
[ -f "$DIR/media.tar.gz" ] || {
  echo "missing $DIR/media.tar.gz" >&2
  exit 1
}
[ -f .env ] || {
  echo "deploy/.env not found — run ./install.sh first." >&2
  exit 1
}
PGPW=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
DIR_ABS="$(cd "$DIR" && pwd)"

dxp() { docker compose exec -T -e PGPASSWORD="$PGPW" db psql -U eeper -d postgres "$@"; }
dxe() { docker compose exec -T -e PGPASSWORD="$PGPW" db psql -U eeper -d eeper "$@"; }

echo "==> Stopping app services (db stays up so we can rebuild it)"
# Everything that may hold a connection to the eeper database. The nudge + fusion workers
# run inside the api, so stopping api covers them; recorder/insight are listed as known
# services (a no-op when not running under the active profile).
docker compose stop api insight recorder >/dev/null 2>&1 || true
docker compose --profile core up -d --wait db

echo "==> Recreating the database"
dxp -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
        WHERE datname = 'eeper' AND pid <> pg_backend_pid();" >/dev/null
dxp -c "DROP DATABASE IF EXISTS eeper;"
dxp -c "CREATE DATABASE eeper OWNER eeper;"

echo "==> Restoring database (TimescaleDB pre/post-restore around pg_restore)"
# TimescaleDB requires the extension present and pre_restore()/post_restore() bracketing
# the load. pg_restore may report ignorable errors (e.g. the extension already exists);
# correctness is confirmed by the data itself, so we don't abort on its exit code.
dxe -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
dxe -tqAc "SELECT timescaledb_pre_restore();" >/dev/null
set +e
docker compose exec -T -e PGPASSWORD="$PGPW" db pg_restore -U eeper -d eeper --no-owner <"$DIR/db.dump"
rc=$?
set -e
[ "$rc" -ne 0 ] && echo "    (pg_restore reported ignorable errors; continuing)"
dxe -tqAc "SELECT timescaledb_post_restore();" >/dev/null

echo "==> Restoring media volume"
# Wipe the volume clean (including dotfiles) then unpack the archive back into it.
docker run --rm \
  -v "${PROJECT}_media-data:/media" \
  -v "${DIR_ABS}:/backup:ro" \
  alpine sh -c "find /media -mindepth 1 -delete && tar xzf /backup/media.tar.gz -C /media"

echo "==> Restore complete. Bring the stack up with ./install.sh"
