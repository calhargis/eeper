# eeper Raspberry Pi CSI camera adapter (arm64-only — Pi hardware). mediamtx's
# native `rpiCamera` source captures the Camera Module (incl. NoIR) via libcamera
# and serves a contract-conformant H.264 RTSP stream the gateway pulls. Capture is
# validated on the [MANUAL] Pi bench; CI only proves the arm64 image builds clean.
# Base pinned by digest (Renovate); mediamtx version+sha Renovate-tracked.

# ── fetch mediamtx (throwaway stage) ──────────────────────────────────────────
FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df AS fetch
ARG MEDIAMTX_VERSION=1.19.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && url="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_arm64.tar.gz" \
 && curl -fsSL "$url" -o /tmp/mediamtx.tgz \
 && echo "562f419912a8668c18216a9e8c95359ec82fbb754e4a44e2953ef62b98eec688  /tmp/mediamtx.tgz" | sha256sum -c - \
 && tar -xzf /tmp/mediamtx.tgz -C /usr/local/bin mediamtx

# ── runtime ───────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df
# libcamera runtime + IPA modules only (no preview/X stack) to keep the surface
# minimal. NOTE (bench): a Raspberry Pi OS deployment may need the Pi-tuned
# libcamera from archive.raspberrypi.com for the Pi ISP pipeline — see the README.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcamera0.0.3 libcamera-ipa \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system app && useradd --system --gid app --uid 10001 --groups video app
COPY --from=fetch /usr/local/bin/mediamtx /usr/local/bin/mediamtx
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh
USER 10001
EXPOSE 8554
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
