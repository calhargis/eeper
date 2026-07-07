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

The `video` profile (M1.1) adds **go2rtc** (media gateway): cameras are registered
via `/api/v1/cameras` (source validated against the H.264/≤1080p contract with
ffprobe), go2rtc re-serves them over internal RTSP, and the api relays WebRTC
signaling. go2rtc is digest-pinned like the db and hardened (non-root, read-only,
cap_drop ALL). Its signaling (`:1984`) and RTSP (`:8554`) control planes stay
**unpublished** — only the WebRTC **media** port `8555` (udp+tcp) is published
(M1.2), because browser media can't traverse an HTTP proxy and must reach go2rtc
directly. It's bound to `EEPER_GO2RTC_CANDIDATE` (the host LAN IP; `127.0.0.1` for
local/CI), not all interfaces. `video-test.yml` is a test-only overlay adding a
synthetic camera on the gateway's network.

The PWA (M1.2) is an installable SvelteKit app (manifest + service worker) with a
**Live view**: WebRTC playback through the api relay, per-camera online/offline
health, and multi-camera switching. Any authenticated household member — including
the `viewer` ("grandparent") role — can watch.

Later phases add more optional profiles (`audio`, `sensors`, `pulseox`, `accel-*`).

## Security defaults

- **Caddy is the only HTTP surface.** `api`/`db` have no host ports; `db` is on an
  `internal:` network with no route off-host. The one other published port is
  go2rtc's WebRTC **media** port `8555` (video profile) — required for browser
  playback, bound to the LAN interface, carrying only DTLS-SRTP media for sessions
  that were already negotiated through the authenticated api relay. Nothing is
  exposed to the internet — use WireGuard/Tailscale for remote access (Master Plan §8).
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
