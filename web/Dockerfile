# eeper web (PWA) — multi-stage: build the static SvelteKit site, serve with Caddy.
# Base images are pinned to immutable digests (Renovate keeps them current).

# ─── build ─────────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4 AS build
WORKDIR /app

# Install deps against the lockfile first for layer caching.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ─── serve ─────────────────────────────────────────────────────────────────
# Pinned to a current Caddy/Alpine rebuild that passes the Trivy CRITICAL gate;
# Renovate keeps this digest fresh (stale bases accrue fixed CVEs over time).
FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS serve
# Non-root on a read-only rootfs: Caddy's writable state goes to /tmp (tmpfs).
ENV XDG_CONFIG_HOME=/tmp/caddy/config \
    XDG_DATA_HOME=/tmp/caddy/data
# Strip the cap_net_bind_service file capability: this server binds 8080 (an
# unprivileged port), and a file-capability binary can't exec under
# no-new-privileges + cap_drop=ALL.
RUN setcap -r /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/build /srv
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD ["wget", "--spider", "-q", "http://127.0.0.1:8080/"]
