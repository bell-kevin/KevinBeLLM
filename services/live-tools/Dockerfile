# SPDX-License-Identifier: AGPL-3.0-or-later
FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-bootstrap.lock requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes --requirement requirements-bootstrap.lock \
    && python -m pip install --no-cache-dir --require-hashes --requirement requirements.lock \
    && addgroup --system --gid 10001 toolserver \
    && adduser --system --uid 10001 --ingroup toolserver --no-create-home toolserver

COPY --chown=10001:10001 app ./app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
