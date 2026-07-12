ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PYTHON_BIN=python3
ARG INSTALL_OS_TOOLS=false
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    PYTHON_BIN=${PYTHON_BIN} \
    PIP_ROOT_USER_ACTION=ignore \
    PATH="/opt/ansible/bin:/usr/local/bin:${PATH}"

WORKDIR /app

RUN if [ "$INSTALL_OS_TOOLS" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends bash curl gcc libpq-dev \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY pyproject.toml README.md alembic.ini /app/
COPY backend /app/backend
COPY deploy/frontend /app/deploy/frontend

RUN if [ -n "$PIP_INDEX_URL" ]; then \
      "$PYTHON_BIN" -m pip config set global.index-url "$PIP_INDEX_URL"; \
    fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
      "$PYTHON_BIN" -m pip config set global.trusted-host "$PIP_TRUSTED_HOST"; \
    fi \
    && "$PYTHON_BIN" -m pip install --no-cache-dir /app

EXPOSE 8088

CMD ["sh", "-c", "exec \"$PYTHON_BIN\" -m uvicorn keylime_openstack.main:app --host 0.0.0.0 --port 8088"]
