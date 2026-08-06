# CLAUDE.md — MedRelay (medical-courier-platform) governance

This file is instructions for any Claude Code session (or other AI coding agent) working in this
repository. Read it in full before making changes. If anything you are asked to do conflicts with
this file, follow this file and flag the conflict to the user rather than silently overriding it.

## What this project is — and is not

MedRelay is a **portfolio/demo prototype** of a B2B healthcare-courier logistics platform for a
Manhattan-Brooklyn service zone, built with **synthetic data only**. It is explicitly:

- **NOT** a real medical delivery operation.
- **NOT** certified or approved for real medical delivery operations.
- **NOT** claiming HIPAA, OSHA, DOT, pharmacy, employment, or any other legal compliance.

Every environment, template, and relevant document must carry this disclaimer verbatim:

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

See `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` for the full compliance-boundary policy and
`docs/PRODUCT_REQUIREMENTS.md` / `docs/ARCHITECTURE_AND_DATA_MODEL.md` /
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md` / `docs/IMPLEMENTATION_ROADMAP.md` for the rest of the
authoritative spec. Those four documents plus this file and
`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` are the governing source of truth for the project;
`docs/CURRENT_STATUS.md` tracks what has actually been built against that spec, phase by phase.

## Operating mode: DEMO_MODE only

The codebase has exactly one supported operating mode today: `DEMO_MODE` (see `APP_MODE` in
`config/settings/base.py`). A future `PILOT_MODE` is referenced throughout the docs as the
eventual real-operation mode, but:

- **Do not implement `PILOT_MODE`** or anything that behaves like a real operating pilot without
  explicit, out-of-band owner approval.
- Do not connect real PHI, real deliveries, real payments, real background checks, or real
  production communications channels under any circumstances in this repository as it stands.
- `docs/IMPLEMENTATION_ROADMAP.md` Phase 10 ("Pilot readiness review, not automatic launch") is a
  hard gate, not a formality — reaching the end of Phase 9 does not authorize a real pilot.

## Architecture: modular Django monolith

- One repository, one deployable Django application, organized as clearly-bounded apps under
  `apps/` (see `docs/ARCHITECTURE_AND_DATA_MODEL.md` for the full logical architecture and entity
  list). Do not split this into microservices or separate repositories during the MVP.
- Each app in `apps/` owns its own models, migrations, and tests, and should stay loosely coupled
  from its siblings — cross-app calls should go through explicit service functions, not reaching
  into another app's internals or ORM querysets directly from views.
- Multi-tenancy is a shared database with explicit `organization_id` scoping on every
  customer-owned entity (once those entities exist, starting Phase 1). Never trust an organization
  ID passed directly from a client without a permission check.
- All datetimes are stored in UTC (`USE_TZ = True`, `TIME_ZONE = "UTC"`) and displayed in
  `America/New_York` in any future UI — this is set in `config/settings/base.py` and must not
  change.

## Zero-cost policy — enforced, not aspirational

This repository must run entirely on free, open-source, locally-hosted software. See
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md` for the full policy, the allowed/prohibited lists, and
the demo-vs-pilot cost distinction.

Practical rules for any future change:

1. **Never add a required dependency** (Python package, JS package, external API, SaaS) that
   requires a paid tier, a credit card, or a real API key to function. Prohibited examples:
   Stripe, Twilio, Auth0/Okta paid tiers, Sentry SaaS, Checkr, paid Mapbox/Google Maps, paid email
   providers. Local/open-source equivalents (Mailpit, Valkey, self-hosted OSRM, mocked adapters)
   are the only acceptable substitutes.
2. Any external capability (routing, notifications, payments, background checks, object storage,
   temperature sensors) must go through an adapter interface (`RoutingProvider`,
   `NotificationProvider`, `PaymentProvider`, `BackgroundCheckProvider`, `ObjectStorageProvider`,
   `TemperatureSensorProvider`) with a local/mock implementation shipped by default. Paid adapters
   are deferred indefinitely, not just "for now."
3. After adding or changing a dependency, run `python manage.py audit_cost`. It fail-closed
   checks `pyproject.toml` dependencies against an explicit allowlist in
   `apps/audit/management/commands/audit_cost.py` and scans `config/` and `apps/` source for
   known prohibited-service indicator strings. If it fails, either the change violates policy, or
   the allowlist in that command needs a deliberate, reviewed update — never widen the allowlist
   just to make a violation disappear without confirming the package is genuinely free/open-source
   and locally runnable.
4. `docs/COST_AUDIT.md` is a **generated file** — it is rewritten by `audit_cost` on every
   successful run. Don't hand-edit it; edit the command's report template if the report format
   needs to change.

## Data minimization and demo-data rules

Per `docs/SECURITY_COMPLIANCE_BOUNDARIES.md`:

- Never add fields for diagnoses, lab results, clinical notes, medication indications, SSNs,
  insurance identifiers, or full patient records — to any app, ever, in any phase.
- Prefer operational references (delivery ID, package barcode, accession/order reference,
  organization/facility IDs, authorized operational contacts) over anything resembling a medical
  record.
- All seed/fixture data under `demo_data/` must be synthetic. No real patient information, real
  prescription information, real courier identity documents, real medical shipment labels, or
  real customer contracts, ever.
- No secrets or credentials committed to Git. `.env` is gitignored; `.env.example` documents names
  only, never real values. GitHub Actions secrets are reserved for cases that genuinely need
  CI-required credentials — ordinary CI (lint/type-check/test/audit) must not require any.

## Do-not-build list (this repository, current phase)

These are explicitly out of scope until (if ever) a real pilot review under
`docs/IMPLEMENTATION_ROADMAP.md` Phase 10 authorizes them:

- Any real background-check integration (Checkr or otherwise).
- Any real payment processing (Stripe or otherwise) — billing stays a synthetic quote/invoice
  prototype per `docs/PRODUCT_REQUIREMENTS.md` §14.
- Any real SMS/paid communications provider — notifications stay in-app + Mailpit + simulated SMS
  events per `docs/PRODUCT_REQUIREMENTS.md` §15.
- Patient transportation, Category A infectious substances, controlled substances, human organs,
  radioactive material, regulated medical waste, loose sharps, unsealed specimens, specialized
  blood products, emergency-response cargo, air shipments, or courier packaging/repacking — all
  explicitly excluded cargo/service types per `docs/PRODUCT_REQUIREMENTS.md` §3.
- Claims of HIPAA/OSHA/DOT/pharmacy/employment/legal compliance anywhere in code, docs, comments,
  or UI copy.
- A real production deployment target/hosting — Phase 9 is "free public demonstration," not
  production.

## Project status (Phase 10 complete — full roadmap built as a demo prototype)

As of Phase 10, every phase in `docs/IMPLEMENTATION_ROADMAP.md` (0 through 10) has been built and
documented. **This remains a portfolio/demo software prototype using synthetic data only** —
completing the roadmap does not change that, and does not authorize a real pilot. See:

- `docs/PILOT_READINESS/GAP_ASSESSMENT.md` — every meaningful gap between this prototype and a real
  operating pilot, organized by domain, cited to specific phases/files.
- `docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` — the professional-review gates
  (`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8) that are hard blockers to any real pilot,
  none resolvable by writing more code.
- `docs/PILOT_READINESS/BUDGET_CHECKLIST.md` — non-binding, order-of-magnitude cost ranges for the
  non-software costs a real pilot would face.
- `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md` — what each zero-cost local/mock provider
  actually does today and what a real, live implementation would need.
- `docs/PILOT_READINESS/GO_NO_GO_REPORT.md` — the overall synthesis: what's solid, what's a hard
  blocker, what's an addressable engineering gap, and a recommended concrete next step. **This
  report does not itself authorize a pilot** — that decision belongs to the project owner, after the
  professional reviews it names.

## Current phase and what exists today

All ten roadmap phases (0 through 10) are built — see "Project status" above. Concretely, that
means:

- Every app under `apps/` has real domain models, migrations, services, and tests — this is no
  longer the "empty, model-free skeleton" state Phase 0 left behind. Follow the entity list in
  `docs/ARCHITECTURE_AND_DATA_MODEL.md` §3 and the existing per-app conventions (service-layer
  functions, tenant scoping via `apps.organizations.services`) when touching any of them.
- `django.db.backends.postgresql` (not the GeoDjango/PostGIS backend) is still the deliberate
  choice — every model that needs coordinates (`Facility`, `CourierLocationPing`, etc.) uses plain
  `DecimalField` lat/lng rather than a PostGIS `PointField`, a decision made in Phase 1 and never
  revisited because no phase ended up needing real spatial queries/indexing. If a future change
  introduces one, that's the moment to add the PostGIS backend and move that model's tests off
  SQLite onto the real `db` compose service.
- SQLite remains the test settings module (`config.settings.test`) database for exactly that
  reason — still valid because nothing tested is PostGIS-specific.
- Django Channels is still deliberately not wired in (see `config/asgi.py`) — no phase ended up
  needing WebSocket push; polling/HTMX covers what was built instead. Revisit only if a real need
  appears.
- A Render+Neon public demo deployment (Phase 9 addendum — see `render.yaml`,
  `config/settings/demo_render.py`, `docs/DEPLOY_RENDER_NEON.md`) has been executed and **is live**
  at `https://medrelay-demo.onrender.com` (Render free web tier + Neon free Postgres, genuinely $0,
  no card). It auto-deploys on every push to `main` — this is unaffected by which machine/account
  pushes, since it's wired to the GitHub repo (`mxhasan03/MedRelay`), not to any local checkout.
  Login: any account in `docs/DEMO_PACKAGE.md` section 3 (e.g. `northstar_owner`, `ops_dispatcher`,
  or a `demo_courier_*` account for the courier PWA) with password `MedRelayDemo!2026`. Free-tier
  cold start after idle can take ~30-50s on the first request.
- Since Phase 10, three post-roadmap work items shipped (new work requested directly by the
  project owner, not from `docs/IMPLEMENTATION_ROADMAP.md`, each independently verified — full
  detail in the dated addenda at the end of `docs/CURRENT_STATUS.md`):
  1. Fixed a real production bug: Django's `{# ... #}` template comment tag doesn't strip
     multi-line comments (only single-line ones), so 5 documentation-heavy `{# #}` blocks across
     4 templates were leaking verbatim into rendered pages. Fixed by converting them to
     `{% comment %}...{% endcomment %}`. A regression test
     (`tests/integration/test_no_multiline_template_comments.py`) now statically scans every
     template for this pattern so it can't silently reappear.
  2. Cleaned up the internal ops dispatch console (`templates/dispatch/*`): card/badge-based
     layout, a real (data-backed) AT RISK vs. INFEASIBLE SLA-risk distinction, previously-invisible
     incident/temperature-alert/courier-location data now surfaced, and query-param-based
     sort/filter on both delivery tables and the ranked-candidate list. Added shared
     `.card`/`.badge-*` classes to `templates/base.html`'s `@layer components`. No live map — out
     of scope for that pass.
  3. Built out the courier PWA (`apps/couriers/*`, `templates/couriers/*`) beyond Phase 5/6: a new
     Availability screen (online/offline, service zone, capacity — over the existing
     `CourierAvailability` model), a new Profile/Onboarding screen (credentials, vehicle, cargo
     authorizations, expiration warnings — read-only, no document upload, per
     `docs/SECURITY_COMPLIANCE_BOUNDARIES.md`), a per-delivery-derived cargo handling boundary
     statement on the active-delivery screen, a visual progress tracker replacing the old plain
     transition list, and a bottom tab bar for app-like navigation. Deliberately kept the courier
     PWA's own `.card`/`.btn` component classes separate from the shared ones added in item 2 above
     (mobile touch-target sizing needs differ) — documented in `templates/couriers/base.html`.
- Always check `docs/CURRENT_STATUS.md` for the exact, current state (file paths, gate output,
  known gaps) before assuming what exists — it is updated at the end of each phase's (and each
  post-roadmap work item's) work and is more current than this file's phase description will
  remain over time.

## Quality gates — must pass before any change is considered done

Run all of these locally before considering work finished (see `README.md` for exact commands):

- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `pytest` (with coverage)
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py audit_cost`
- `detect-secrets-hook --baseline .secrets.baseline $(git ls-files)` (update the baseline
  deliberately via `detect-secrets scan --baseline .secrets.baseline` — which rewrites the file
  in place — if a new *non-secret* false positive appears; never add a baseline entry to hide a
  real secret — remove the secret instead)

CI (`.github/workflows/ci.yml`) runs the same gates and must not require secrets or external
credentials. Installing public packages or pulling public Docker images over the network during
CI is fine; needing an API key or credential is not.

## Practical conventions

- Dependency management is `uv` + `pyproject.toml` + `uv.lock`. Add dependencies via
  `uv add <package>` (or `uv add --group dev <package>` for dev-only tools), not by hand-editing
  the lock file. Then update the allowlist in `apps/audit/management/commands/audit_cost.py` and
  rerun `audit_cost`.
- New Django apps go under `apps/<name>/` and must be registered in `INSTALLED_APPS` in
  `config/settings/base.py` with an `apps.<name>` dotted path, matching the existing apps.
- Keep `docs/PRODUCT_REQUIREMENTS.md`, `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`,
  `docs/ARCHITECTURE_AND_DATA_MODEL.md`, `docs/SECURITY_COMPLIANCE_BOUNDARIES.md`, and
  `docs/IMPLEMENTATION_ROADMAP.md` as the authoritative spec — if implementation needs to diverge
  from them, update the doc and explain why in `docs/CURRENT_STATUS.md`, don't just silently drift.
- `docs/CURRENT_STATUS.md` should be updated at the end of meaningful units of work: what was
  done, exact file paths touched, evidence/output of the quality gates, known gaps or deviations,
  and the resulting commit hash.
