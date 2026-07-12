ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG INSTALL_OS_TOOLS=false
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

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
      python -m pip config set global.index-url "$PIP_INDEX_URL"; \
    fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
      python -m pip config set global.trusted-host "$PIP_TRUSTED_HOST"; \
    fi \
    && python -m pip install --no-cache-dir -e /app

EXPOSE 8088

CMD ["uvicorn", "keylime_openstack.main:app", "--host", "0.0.0.0", "--port", "8088"]
