# deploy — packaging & deployment

The Docker Compose `core` stack, `install.sh`, and local TLS. The whole system
runs with one `./install.sh` on any amd64/arm64 Linux box. See the full guide:
[docs/install.md](../docs/install.md).

## The `core` stack (M0.2)

| Service | Image                    | Role                                             |
| ------- | ------------------------ | ------------------------------------------------ |
| `caddy` | `deploy/caddy` (built)   | Edge proxy: TLS (local CA), HTTP→HTTPS, headers  |
| `api`   | `server` (built)         | FastAPI: first-boot wizard, auth, `/api/v1/*`    |
| `web`   | `web` (built)            | Static PWA (served internally, proxied by Caddy) |
| `db`    | `timescale/timescaledb`  | PostgreSQL + TimescaleDB (internal-only network) |

Later phases add optional Compose profiles (`video`, `audio`, `sensors`,
`pulseox`, `accel-*`).

## Security defaults

- **Only Caddy publishes ports.** `api`/`db` have no host ports; `db` is on an
  `internal:` network with no route off-host. Nothing is exposed to the internet
  — use WireGuard/Tailscale for remote access (Master Plan §8).
- **Every container** runs non-root, read-only rootfs, `cap_drop: ALL`,
  `no-new-privileges`. Caddy binds unprivileged 8080/8443 inside the container
  (host 80/443 map onto them), so it needs zero capabilities.
- **No default credentials.** `install.sh` generates `deploy/.env` (git-ignored);
  the first-boot wizard forces admin creation.

## Files

- `docker-compose.yml` — the `core` stack with hardening.
- `install.sh` — prereq check, secret generation, build+up, CA extraction.
- `.env.example` — template (real values live in the generated `.env`).
- `caddy/` — the edge proxy image + `Caddyfile`.
- `tests/` — integration tests asserting the M0.2 criteria (run by CI's `stack` workflow).
