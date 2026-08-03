# MedRelay (medical-courier-platform)

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

MedRelay is a portfolio/demo B2B healthcare-courier logistics platform prototype for New York City
(Manhattan-Brooklyn zone). It is being built in phases; **this repository currently contains Phase
0 (repository foundation) only** — no domain models, no real delivery workflow yet. See
`docs/IMPLEMENTATION_ROADMAP.md` for the full plan and `docs/CURRENT_STATUS.md` for exactly what is
done today.

The project runs entirely on free, open-source, locally-hosted software (`DEMO_MODE`). See
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md` and `docs/COST_AUDIT.md`.

## Stack

Python 3.12, Django 5.2 LTS, Django REST Framework, drf-spectacular, PostgreSQL 17 + PostGIS 3.5,
Valkey, Celery 5.6, Mailpit, HTMX + Alpine.js + Tailwind (templates), `uv` for dependency
management, GitHub Actions CI.

## Prerequisites

- Docker Engine + the `docker compose` plugin (tested path). Podman + `podman-compose` should also
  work against the same `compose.yaml` but is not the tested path in this repo.
- Python 3.12 if you want to run tooling (tests/lint/type-check) outside containers.
- [`uv`](https://docs.astral.sh/uv/) if you want to manage the Python environment locally
  (`pip3 install --user uv`, or see uv's install docs). Not required to run the compose stack.

## Quick start — run the full local stack

```bash
cp .env.example .env
docker compose up --build
```

This starts:

- `web` — Django dev server on <http://localhost:8000> (`/healthz/`, `/readyz/`, `/api/schema/`, `/api/docs/`)
- `db` — PostgreSQL 17 + PostGIS 3.5 (`postgis/postgis:17-3.5`)
- `valkey` — Valkey (Redis-compatible) cache/broker
- `worker` — Celery worker (no scheduled tasks in Phase 0)
- `mailpit` — local SMTP + web UI on <http://localhost:8025>

Tear down with `docker compose down` (add `-v` to also drop the database volume).

Validate the compose file without starting anything:

```bash
docker compose config
```

## Local development without containers (for running quality gates)

Phase 0 CI and local quality gates use SQLite (`config.settings.test`) so no Postgres/PostGIS
server is required to run them. This is a deliberate, temporary decision — see
`docs/CURRENT_STATUS.md` and `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`.

```bash
pip3 install --user uv          # one-time, if uv is not already installed
export PATH="$HOME/.local/bin:$PATH"

uv sync --group dev             # creates .venv and installs all dependencies
source .venv/bin/activate

export DJANGO_SETTINGS_MODULE=config.settings.test

ruff check .
ruff format --check .
mypy .
pytest
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py audit_cost
detect-secrets-hook --baseline .secrets.baseline $(git ls-files)
```

If `uv` cannot reach PyPI in your environment, fall back to plain `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e . --group dev   # pip >= 25.1, or see requirements.lock.txt fallback below
```

## Health endpoints

- `GET /healthz/` — liveness; always returns `200` with no dependency checks.
- `GET /readyz/` — readiness; returns `200` only when the database and cache are reachable, `503`
  otherwise.

## Repository layout

See `docs/ARCHITECTURE_AND_DATA_MODEL.md` for the target architecture and
`docs/IMPLEMENTATION_ROADMAP.md` for what each phase delivers. Phase 0 provides the Django project
skeleton, one empty modular app per planned domain (no models yet), the compose stack, CI, and the
zero-cost audit tooling.

## Zero-cost policy

No required paid software, API, cloud database, mapping, SMS, payment, or identity provider is used
anywhere in this repository. Run `python manage.py audit_cost` to verify; see
`docs/COST_AUDIT.md` for the generated report and `docs/TECH_STACK_AND_ZERO_COST_POLICY.md` for the
full policy and allowlist.

## Governance

Read `CLAUDE.md` before making changes — it documents the modular-monolith architecture, the
zero-cost policy, the demo-data-only rule, and the do-not-build list that keep this prototype from
drifting into claiming real operational capability.
