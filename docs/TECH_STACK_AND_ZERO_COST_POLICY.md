# Technical Stack and Zero-Cost Policy

## 1. Architecture choice

Use a **modular Django monolith** with one repository and one deployable application during the MVP. This reduces infrastructure, authentication, deployment, and cross-service complexity while preserving clear module boundaries.

## 2. Core stack

### Language and framework

- Python 3.12
- Django 5.2 LTS, pinned to the latest security patch in the 5.2 line
- Django REST Framework for versioned APIs
- drf-spectacular for OpenAPI documentation

### Frontend

- Django templates
- HTMX for partial-page interactions
- Alpine.js for small client-side state
- Tailwind CSS for the design system
- Progressive Web App manifest/service worker for courier mobile use
- Minimal custom JavaScript; no mandatory React/Next.js frontend in Version 1

### Data

- PostgreSQL 17+ as the relational database
- PostGIS 3.5+ for spatial data, service zones, distances, and location queries
- SQLite allowed only for limited unit tests when PostgreSQL-specific behavior is not being tested

### Background and real-time processing

- Celery 5.6 for background tasks and scheduling
- Valkey as the open-source cache/message broker
- Django Channels for WebSocket status/location updates

### Maps and routing

- MapLibre GL JS for browser map rendering
- OpenStreetMap-derived data with required attribution
- OSRM self-hosted/local for routing in the demo
- PostGIS for spatial filtering

Do not depend on public community map, tile, geocoding, or routing endpoints for production-scale traffic. The prototype may use local fixtures or self-hosted services.

### Files and exports

- Local filesystem in development
- MinIO as an optional local S3-compatible object store
- WeasyPrint is optional only if its system dependencies remain fully local/free; otherwise use HTML/CSV exports first

### Authentication and authorization

- Django authentication
- Custom organization membership and role models
- django-otp for optional TOTP MFA
- Short-lived signed recipient links/tokens
- No paid identity provider

### Barcode and scanning

- Segno or qrcode for QR generation
- ZXing-based browser scanning or a small open-source JavaScript scanner
- Manual code entry fallback

### Testing and quality

- pytest
- pytest-django
- factory_boy
- Hypothesis for state-machine/property tests where useful
- Playwright for end-to-end browser tests
- Ruff for linting/formatting
- mypy with Django typing support
- coverage.py
- axe-core/Playwright accessibility checks

### Development and containers

- `uv` with `pyproject.toml` and lock file
- Podman Compose preferred for a fully free/open-source desktop workflow
- Docker Compose files may also be supplied, but Docker Desktop must not be a mandatory dependency
- Mailpit for local email capture

### CI/CD

- GitHub Actions
- Ordinary CI must not require secrets or external network calls
- Public repositories can use standard GitHub-hosted Actions without usage charges; private repositories are subject to included quotas
- Add a fail-closed dependency/cost audit
- Do not configure paid runners or artifact retention that risks charges

## 3. Repository layout

```text
medical-courier-platform/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── compose.yaml
├── .env.example
├── .github/workflows/
│   ├── ci.yml
│   └── security.yml
├── config/
│   ├── settings/
│   ├── urls.py
│   ├── asgi.py
│   └── celery.py
├── apps/
│   ├── accounts/
│   ├── organizations/
│   ├── facilities/
│   ├── couriers/
│   ├── cargo/
│   ├── deliveries/
│   ├── dispatch/
│   ├── custody/
│   ├── tracking/
│   ├── temperature/
│   ├── incidents/
│   ├── notifications/
│   ├── billing/
│   ├── reporting/
│   └── audit/
├── templates/
├── static/
├── frontend/
│   ├── css/
│   └── js/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── accessibility/
├── docs/
├── scripts/
└── demo_data/
```

## 4. Strict zero-cost prototype policy

### Allowed

- open-source local software
- GitHub Free and included GitHub Actions allowance
- local PostgreSQL/PostGIS
- local Valkey/Celery
- local OSRM/MapLibre/OpenStreetMap data
- local Mailpit
- local MinIO
- synthetic fixtures
- manual CSV imports/exports
- mocked integrations

### Prohibited as required dependencies

- Twilio or paid SMS
- Google Maps Platform
- paid Mapbox API
- Stripe or paid payment processing
- Auth0/Okta paid services
- Sentry SaaS
- Checkr/background-check API
- paid cloud object storage
- paid e-signature
- paid temperature IoT platform
- paid email service
- card-required trial that may create charges

### Adapter rule

Every external capability must use an interface such as:

- `RoutingProvider`
- `NotificationProvider`
- `PaymentProvider`
- `BackgroundCheckProvider`
- `ObjectStorageProvider`
- `TemperatureSensorProvider`

Version 1 ships with local/mock implementations. Paid or enterprise adapters are deferred.

## 5. Demo versus real pilot

### Fully free demo

The demo can be built and run for $0 using synthetic data on a developer-controlled machine.

### Real operating pilot

A real medical-courier operation cannot responsibly be promised at $0. Likely unavoidable non-software costs include:

- legal/compliance review
- insurance
- background and motor-vehicle checks
- courier equipment/PPE
- operational staff
- reliable SMS/communications
- mapping/routing infrastructure at scale
- production hosting/backups
- payment processing
- potential security/compliance assessments

The repository must clearly distinguish `DEMO_MODE` from any future `PILOT_MODE`.

## 6. Cost audit

Implement:

```bash
python manage.py audit_cost
```

The command must:

- inspect dependencies against an approved allowlist
- inspect configuration for prohibited required services
- fail when an unreviewed external dependency appears
- produce `docs/COST_AUDIT.md`
- never claim operational business costs are zero
