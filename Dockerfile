# MedRelay web/worker image — Phase 0.
# Used for both the `web` (Django dev server) and `worker` (Celery) compose
# services; the command differs per-service in compose.yaml. Also used,
# unchanged, as the Render (Phase 9 hosting decision) `web` service image —
# see render.yaml and docs/DEPLOY_RENDER_NEON.md — via render.yaml's own
# `dockerCommand` override (below), not by changing this file's default CMD.
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

# Shell form (not exec-form JSON array) deliberately, so $PORT is expanded by
# the shell at container start — Docker's exec form (`CMD ["a", "b"]`) never
# expands environment variables, which would otherwise silently ignore a
# platform-injected $PORT. This default (the local dev server, falling back
# to 8000 when $PORT is unset — e.g. plain `docker run` with no override)
# exists so this image is correct even when run directly with
# `docker run -e PORT=<n> <image>` and no explicit command; render.yaml's
# `dockerCommand` overrides this default in the actual Render deployment
# with the real migrate+seed+gunicorn start sequence (see
# docs/DEPLOY_RENDER_NEON.md), and compose.yaml's own `command:` overrides
# it for local dev/worker use — this default is the one case (a bare
# `docker run`) neither of those covers.
CMD python manage.py runserver 0.0.0.0:${PORT:-8000}
