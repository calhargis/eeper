# eeper USB (V4L2) camera adapter. Reads a UVC webcam (or, in CI, a synthetic
# lavfi source) and serves a contract-conformant H.264 RTSP stream the gateway
# pulls. mediamtx is the RTSP listener; ffmpeg (launched via runOnInit) is the
# encoder. Multi-arch (amd64 + arm64). Base images pinned by digest (Renovate).

# ── fetch mediamtx (throwaway stage; keeps curl/tar out of the runtime layer) ──
FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df AS fetch
ARG TARGETARCH
ARG MEDIAMTX_VERSION=1.19.2
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && case "$TARGETARCH" in \
      amd64) sha=f9c601cc303ceca8fad2883917b022882672c5bc56311e92dbceb16e5f20c60c ;; \
      arm64) sha=562f419912a8668c18216a9e8c95359ec82fbb754e4a44e2953ef62b98eec688 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && url="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_${TARGETARCH}.tar.gz" \
 && curl -fsSL "$url" -o /tmp/mediamtx.tgz \
 && echo "${sha}  /tmp/mediamtx.tgz" | sha256sum -c - \
 && tar -xzf /tmp/mediamtx.tgz -C /usr/local/bin mediamtx

# ── runtime ───────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim@sha256:60eac759739651111db372c07be67863818726f754804b8707c90979bda511df
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system app && useradd --system --gid app --uid 10001 app
COPY --from=fetch /usr/local/bin/mediamtx /usr/local/bin/mediamtx
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh
USER 10001
EXPOSE 8554
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
