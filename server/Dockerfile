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
    PATH="/opt/venv/bin:$PATH"
# Non-root runtime user (read-only rootfs is enforced by compose; only /tmp is tmpfs).
RUN groupadd --system app && useradd --system --gid app --uid 10001 app
COPY --from=build /opt/venv /opt/venv
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"]
CMD ["uvicorn", "eeper.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
