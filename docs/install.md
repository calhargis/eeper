# Installing eeper

> Phase 0 status: this brings up a **secure, empty, login-gated** stack (no
> camera/monitoring features yet — those arrive in Phase 1+).

## Prerequisites

- An amd64 or arm64 Linux host (Raspberry Pi 4/5, a NAS, an old laptop, a mini-PC, or a VM)
- **Docker** + Compose v2
- `openssl` (to generate secrets)

## Install

```bash
git clone https://github.com/calhargis/eeper.git
cd eeper/deploy
./install.sh
```

`install.sh`:

1. checks prerequisites,
2. generates random secrets into `deploy/.env` (there are **no default credentials**),
3. builds the images and starts the `core` stack (Caddy, API, TimescaleDB, web),
4. extracts the local CA certificate to `deploy/eeper-local-ca.crt`, and
5. prints the first-boot wizard URL.

It is safe to re-run — existing secrets in `deploy/.env` are preserved.

## Trust the local CA, then finish setup

eeper serves HTTPS with a **locally generated CA** (Caddy's internal issuer), so
LAN traffic is encrypted. Trust `deploy/eeper-local-ca.crt` on each device that
will view the monitor — see [docs/testing/m0.2-ca-trust.md](./testing/m0.2-ca-trust.md).

Then open the printed URL (e.g. `https://localhost/` in dev, or
`https://<your-domain>/`) and complete the **first-boot wizard**: it forces you
to create an admin account. Until you do, every protected endpoint returns 401.

## Configuration (`deploy/.env`)

| Variable                                 | Purpose                                                         |
| ---------------------------------------- | --------------------------------------------------------------- |
| `EEPER_DOMAIN`                           | Hostname/IP Caddy serves and issues the local-CA cert for       |
| `EEPER_BIND_ADDR`                        | Host interface Caddy binds to (set to your LAN IP for LAN-only) |
| `EEPER_HTTP_PORT` / `EEPER_HTTPS_PORT`   | Published ports (default 80 / 443)                              |
| `POSTGRES_PASSWORD` / `EEPER_SECRET_KEY` | Generated secrets — never edit by hand                          |

## Security posture (defaults)

- **Nothing is exposed to the internet.** Only Caddy publishes ports; the API and
  database have no host ports and the database sits on an internal-only network.
  For remote access use WireGuard/Tailscale — **never** port-forward (Master Plan §8).
- **TLS everywhere**, including LAN, via the local CA.
- **Every container** runs non-root on a read-only root filesystem with all Linux
  capabilities dropped.
- **No default credentials** anywhere; the first-boot wizard forces admin creation.
