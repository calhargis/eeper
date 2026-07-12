# eeper api (FastAPI) — multi-stage, non-root, read-only-rootfs friendly.
# Base pinned to a CRITICAL-clean digest; Renovate keeps it fresh.

# ─── build ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /src
RUN python -m venv /opt/venv
# Install the eeper package (runtime deps only) into the venv.
COPY pyproject.toml README.md ./
COPY eeper ./eeper
RUN pip install .

# ─── runtime ───────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp \
    TMPDIR=/tmp
# Cache-bust for the apt layer: bump this date when a base package gets a CVE fix but
# the pinned base digest hasn't rotated, so the gha build cache rebuilds this layer and
# pulls the patched package instead of reusing a stale one (e.g. libgbm1 CVE-2026-40393,
# fixed in +deb12u2, reached via a fresh apt on the same base).
ARG APT_SECURITY_EPOCH=2026-07-12
# ffprobe validates camera streams against the RTSP contract (codec + resolution).
RUN echo "apt security epoch ${APT_SECURITY_EPOCH}" \
  && apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --system app && useradd --system --gid app --uid 10001 app \
  # Own /media in the image so a fresh media-data named volume inherits 10001
  # ownership on first mount (same pattern the db uses for PGDATA), letting the
  # non-root recorder/api write segments + clips under a read-only rootfs.
  && mkdir -p /media/rec /media/clips \
  && chown -R app:app /media
COPY --from=build /opt/venv /opt/venv
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"]
CMD ["uvicorn", "eeper.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
