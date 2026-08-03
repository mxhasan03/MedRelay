# MedRelay web/worker image — Phase 0.
# Used for both the `web` (Django dev server) and `worker` (Celery) compose
# services; the command differs per-service in compose.yaml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq-dev + build-essential are needed to build psycopg from source on
# some platforms; psycopg-binary wheels normally avoid this, but keeping the
# system libpq client library present is still useful for `pg_isready`-style
# debugging and covers platforms without a matching wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV DJANGO_SETTINGS_MODULE=config.settings.dev

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
