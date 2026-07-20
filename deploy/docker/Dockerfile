ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PYTHON_BIN=python3
ARG INSTALL_OS_TOOLS=true
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
      if command -v apt-get >/dev/null 2>&1; then \
        apt-get update \
        && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
          bash curl gcc libpq-dev openssh-client \
        && rm -rf /var/lib/apt/lists/*; \
      elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y bash curl gcc postgresql-devel openssh-clients \
        && dnf clean all; \
      elif command -v microdnf >/dev/null 2>&1; then \
        microdnf install -y bash curl gcc postgresql-devel openssh-clients \
        && microdnf clean all; \
      elif command -v yum >/dev/null 2>&1; then \
        yum install -y bash curl gcc postgresql-devel openssh-clients \
        && yum clean all; \
      elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache bash curl gcc musl-dev postgresql-dev openssh-client; \
      else \
        echo "INSTALL_OS_TOOLS=true but no supported package manager was found" >&2; \
        exit 1; \
      fi; \
    fi \
    && if ! command -v ssh >/dev/null 2>&1; then \
      echo "OpenSSH client is required for Keylime/OpenStack policy deployment. Set INSTALL_OS_TOOLS=true or use a base image that already contains ssh." >&2; \
      exit 1; \
    fi

COPY pyproject.toml README.md alembic.ini /app/
COPY backend /app/backend
COPY deploy/frontend /app/deploy/frontend
COPY deploy/ansible /app/deploy/ansible

RUN if [ -n "$PIP_INDEX_URL" ]; then \
      "$PYTHON_BIN" -m pip config set global.index-url "$PIP_INDEX_URL"; \
    fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
      "$PYTHON_BIN" -m pip config set global.trusted-host "$PIP_TRUSTED_HOST"; \
    fi \
    && "$PYTHON_BIN" -m pip install --no-cache-dir /app

EXPOSE 8088

CMD ["sh", "-c", "exec \"$PYTHON_BIN\" -m uvicorn keylime_openstack.main:app --host 0.0.0.0 --port 8088"]
