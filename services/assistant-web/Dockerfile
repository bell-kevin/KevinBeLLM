# SPDX-License-Identifier: AGPL-3.0-or-later
FROM docker.io/library/python:3.13.15-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    APP_HOST=127.0.0.1 \
    APP_PORT=3000

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock \
    && addgroup --system --gid 10001 assistant \
    && adduser --system --uid 10001 --ingroup assistant --home /nonexistent assistant \
    && install -d -o assistant -g assistant -m 0700 /data

COPY --chown=assistant:assistant app ./app
COPY --chown=assistant:assistant static ./static

USER 10001:10001
EXPOSE 3000

CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$APP_HOST\" --port \"$APP_PORT\" --workers 1 --no-access-log"]
