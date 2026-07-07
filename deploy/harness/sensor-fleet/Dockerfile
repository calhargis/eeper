# Synthetic sensor fleet (test harness). Non-root, read-only-rootfs friendly.
FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "paho-mqtt>=2.1,<3.0" \
  && groupadd --system app && useradd --system --gid app --uid 10001 app
COPY publish.py /app/publish.py
USER app
CMD ["python", "/app/publish.py"]
