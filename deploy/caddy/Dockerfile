# eeper edge reverse proxy. Runs fully hardened: non-root, read-only rootfs, and
# zero Linux capabilities. Caddy binds unprivileged ports 8080/8443 inside the
# container; compose maps host 80/443 onto them. The file capability is stripped
# because a file-capability binary can't exec under no-new-privileges.
FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648

# Config autosave goes onto the writable /data volume so rootfs stays read-only.
ENV XDG_CONFIG_HOME=/data/config
COPY Caddyfile /etc/caddy/Caddyfile

# Validate at build (fails the build on a bad Caddyfile). Validation provisions
# `tls internal`, which writes a throwaway CA to /data AS ROOT — remove it so the
# runtime volume starts clean and fully owned by the non-root user, then strip
# the file capability (can't exec a file-capability binary under no-new-privs).
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
  && rm -rf /data/caddy /data/config \
  && mkdir -p /data/config \
  && chown -R 10001:10001 /data /config \
  && setcap -r /usr/bin/caddy

USER 10001
EXPOSE 8080 8443
