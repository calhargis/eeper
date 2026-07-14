# Backup & restore

eeper keeps all durable state in two places, and a backup captures both:

| What                                                                         | Where                              | Backed up as   |
| ---------------------------------------------------------------------------- | ---------------------------------- | -------------- |
| Database (users, events, trends, sleep sessions, sensor + pulse-ox readings) | the `db-data` volume (TimescaleDB) | `db.dump`      |
| Media (recording ring buffer + promoted clips)                               | the `media-data` volume            | `media.tar.gz` |

Both scripts live in `deploy/` and run against the Compose stack — no extra tooling
beyond Docker.

## Back up

```bash
cd deploy
./backup.sh                 # → deploy/backups/<UTC-timestamp>/
./backup.sh /path/to/dest   # …or a directory you choose
```

This runs `pg_dump` in the database container (custom format) and tars the media volume
read-only, so it is safe to run while the stack is live. The output directory holds
`db.dump` and `media.tar.gz`.

Copy the backup directory somewhere off the host (another disk, a NAS, object storage).
A simple nightly cron:

```cron
30 4 * * *  cd /opt/eeper/deploy && ./backup.sh >> /var/log/eeper-backup.log 2>&1
```

Prune old backups with your own retention (e.g. `find deploy/backups -maxdepth 1 -mtime +30 -type d`).

## Restore

> **Destructive.** Restore drops and recreates the `eeper` database and replaces the
> media volume. Do it on a fresh host or when you intend to roll back.

```bash
cd deploy
./restore.sh backups/<timestamp>
./install.sh          # bring the stack back up
```

`restore.sh` stops the app services (a database drop needs no live connections), brings
up only the database, recreates it, and loads the dump. Because the database is
TimescaleDB, the load is bracketed by `timescaledb_pre_restore()` /
`timescaledb_post_restore()` — the scripts handle this for you. `pg_restore` may print a
few ignorable notices (for example, that the `timescaledb` extension already exists);
those are expected and correctness is confirmed by the data itself. The media volume is
wiped and unpacked from `media.tar.gz`.

Continuous aggregates, compression, and retention policies all survive the round trip —
the schema comes back exactly as it was, and the api re-attaches to it on the next
`./install.sh` (schema creation is idempotent, so it is a no-op over restored data).

## What is verified in CI

The `backup-restore` job proves the round trip end to end on every change: it seeds a
stack (database rows across the hypertables plus a media file), backs it up, destroys the
volumes, restores into a fresh stack, and asserts the database digest and the media
checksum are **identical** to what was backed up.

## Move to new hardware

1. `./backup.sh` on the old host and copy the backup directory to the new host's
   `deploy/backups/`.
2. On the new host, `./install.sh` once to generate `deploy/.env` (secrets), then stop
   it: `docker compose --profile core down`.
3. `./restore.sh backups/<timestamp>` then `./install.sh`.

Keep `deploy/.env` from the old host **only** if you also want the old signing/VAPID
secrets; otherwise the fresh `.env` is fine — the restored data does not depend on it.
