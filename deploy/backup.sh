#!/usr/bin/env bash
# eeper backup — a consistent snapshot of everything a restore needs:
#   * db.dump      — pg_dump (custom format) of the TimescaleDB database
#   * media.tar.gz — the media volume (recording ring buffer + promoted clips)
#
# Writes a timestamped directory under deploy/backups/ (or a path you pass as $1).
# Restore with restore.sh. See docs/operations/backup-restore.md.
set -euo pipefail

cd "$(dirname "$0")"
PROJECT=eeper # matches `name:` in docker-compose.yml → volumes are ${PROJECT}_*

if [ ! -f .env ]; then
  echo "deploy/.env not found — run ./install.sh first." >&2
  exit 1
fi
PGPW=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)

OUT="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
OUT_ABS="$(cd "$OUT" && pwd)"

echo "==> Dumping database → $OUT/db.dump"
# Custom format so restore.sh can drive TimescaleDB's pre/post-restore around it.
docker compose exec -T -e PGPASSWORD="$PGPW" db pg_dump -U eeper -Fc eeper >"$OUT/db.dump"

echo "==> Archiving media volume → $OUT/media.tar.gz"
# Read the named volume directly (mounted read-only) via a throwaway container, so a
# backup works whether or not the recorder/api are running.
docker run --rm \
  -v "${PROJECT}_media-data:/media:ro" \
  -v "${OUT_ABS}:/backup" \
  alpine tar czf /backup/media.tar.gz -C /media .

echo "==> Backup complete: $OUT"
ls -la "$OUT"
