FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini /app/
COPY backend /app/backend
COPY deploy/frontend /app/deploy/frontend

RUN pip install --no-cache-dir -e /app

EXPOSE 8088

CMD ["uvicorn", "keylime_openstack.main:app", "--host", "0.0.0.0", "--port", "8088"]
