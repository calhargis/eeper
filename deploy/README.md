# deploy — packaging & deployment

Docker Compose stack, `install.sh`, and local TLS provisioning. The whole
system runs with one `docker compose up` on any amd64/arm64 Linux box.

Planned (Phase 0, M0.2):

- `docker-compose.yml` with the `core` profile (caddy, api, timescaledb, web).
- `install.sh` — prerequisite check, secret generation, local CA provisioning,
  first-boot wizard URL.
- Compose **profiles** (`core`, `video`, `audio`, `sensors`, `pulseox`,
  `accel-*`) mapping to hardware reality.

Nothing here binds to a public interface by default; remote access is via
WireGuard/Tailscale only (see Master Plan §8).
