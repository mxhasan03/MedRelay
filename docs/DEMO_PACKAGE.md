# Demo Package — Phase 9 "Free Public Demonstration Option"

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

This document is the real, tested "run the whole thing end to end" walkthrough for MedRelay as a
**local, self-contained demo package**. It is deliberately scoped to *local* execution
(`docker compose up --build` on your own machine) — see `docs/HOSTING_OPTIONS.md` for the separate,
explicitly-not-yet-decided question of whether/where to run a *publicly reachable* copy of this
same package. **No external hosting account was created and nothing was deployed to any
third-party service as part of building this document or this phase** — see
`docs/CURRENT_STATUS.md` "Phase 9" for the full scope statement.

## 1. What "the demo package" means here

Everything needed to stand up a fully working copy of MedRelay already lives in this repository and
runs on free, self-hosted software only (`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`):

- `Dockerfile` — one image, used for both the `web` (Django) and `worker` (Celery) containers.
- `compose.yaml` — `web`, `worker`, `db` (PostgreSQL 17 + PostGIS 3.5), `valkey` (cache/broker),
  `mailpit` (local SMTP capture + web UI). No service in this file requires a credential, API key,
  or payment method to start.
- `config/settings/{dev,test,prod,demo}.py` — see section 4 below for what `demo.py` is (and is
  not) for.
- `apps/organizations/management/commands/{seed_demo_data,seed_full_demo,reset_demo_data}.py` — the
  synthetic dataset and its reset mechanism (sections 3 and 5 below).

This package makes no assumption about *which* host eventually runs it (a laptop, a colleague's
machine, or — if the project owner later decides, see `docs/HOSTING_OPTIONS.md` — a specific free
hosting platform). Every environment-specific value (database credentials, allowed hosts, secret
key, SMTP host) is read from environment variables (`.env`, `django-environ`), never hardcoded to a
specific hostname or provider.

## 2. Real, tested walkthrough

This exact sequence was run in this session against Docker Engine 29.1.3 (`docker compose` v2
plugin) on the machine building this phase, with the real `postgis/postgis:17-3.5` image (host
ports remapped only because this shared dev machine already had unrelated services on
5432/6379/8000 — not needed on a clean machine).

```bash
cp .env.example .env
docker compose up --build
```

Wait for `db`/`valkey` to report healthy (compose's own healthchecks gate `web`/`worker`'s startup),
then in a second terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_full_demo
```

Real output from this session (abbreviated — see `docs/CURRENT_STATUS.md` "Phase 9" for the full
transcript):

```
$ docker compose exec web python manage.py migrate
Operations to perform:
  Apply all migrations: accounts, admin, audit, auth, billing, cargo, contenttypes, couriers,
  custody, deliveries, dispatch, facilities, incidents, notifications, organizations, otp_totp,
  recipient, reporting, sessions, temperature, tracking
Running migrations:
  ... (52 migrations) ...

$ docker compose exec web python manage.py seed_full_demo
Seeded 3 organizations, 8 facilities, 18 customer-org memberships, 7 internal-staff users. Demo
login password for every seeded user: 'MedRelayDemo!2026' (synthetic, not a real secret).
Generated invoice INV-000001 for the delivered demo scenario.
Seeded new demo scenarios: ready_for_dispatch, assigned, delivered_full_chain (+ invoice),
temperature_excursion, recipient_unavailable_return.
```

Then, in a browser (or via `curl`, as verified in this session):

- Visit <http://localhost:8000/accounts/login/>.
- Log in as `northstar_owner` / `MedRelayDemo!2026` (see section 3 for the full account list).
- You land on `/organizations/` and see "NorthStar Diagnostics (Demo)" — a real, tenant-scoped
  authenticated session, not a static mockup. The bold red `DEMO_MODE` banner and full compliance
  disclaimer render at the top of every page (verified by inspecting the real HTML response).
- Mailpit's web UI (<http://localhost:8025>) shows any outbound email captured locally, never sent
  anywhere real.

Health endpoints (also verified in this session against the real containerized stack):

```
$ curl http://localhost:8000/healthz/
{"status": "ok"}
$ curl http://localhost:8000/readyz/
{"status": "ok", "checks": {"database": "ok", "cache": "ok"}}
```

Tear down with `docker compose down` (add `-v` to also drop the database/cache volumes — do this
between demo resets if you want a completely clean slate instead of using `reset_demo_data`,
section 5).

## 3. Demo accounts (design decision: pre-seeded, not self-registration)

**Decision: a small set of pre-seeded demo login accounts covering each major role, not a
self-service registration flow.** Documented reasoning:

- This application's most interesting behavior is its *state-mutating workflows* — assigning a
  courier, advancing a delivery through pickup/transit, capturing custody proof, opening/resolving
  an incident, generating an invoice. A visitor who can log in as a dispatcher and click "Assign"
  on a real `READY_FOR_DISPATCH` delivery sees far more of what this prototype actually does than
  one who has to first build a tenant from scratch through a signup form.
- A self-registration flow that creates *real* organizations/facilities/couriers would need its own
  new quota/abuse-safeguard surface (rate-limited signup, email verification with a real mail
  provider this project's zero-cost policy prohibits, etc.) for comparatively little demo value —
  every role's interesting screens are already reachable through a pre-seeded account.
- Every seeded account already has a role-appropriate, realistic amount of pre-existing data (the
  five delivery-request scenarios in section 4) to explore immediately, which a fresh
  self-registered account would not.
- This mirrors the same reasoning `docs/CURRENT_STATUS.md`'s Phase 1 section gave for
  `seed_demo_data` in the first place ("account provisioning is inherently an admin/sales-onboarding
  action... not a public signup flow") — Phase 9 extends that decision to the public-demo context
  rather than reversing it.

The trade-off, stated honestly: every visitor to a hypothetical public deployment shares the same
handful of accounts and the same underlying data, and one visitor's actions (e.g. resolving the
seeded temperature-excursion incident) are visible to the next. `reset_demo_data` (section 5) is the
mitigation — an operator can restore a clean, deterministic dataset on a schedule.

### Account list

All accounts share one synthetic password: **`MedRelayDemo!2026`**
(`# pragma: allowlist secret` — not a real credential; the exact same convention as every prior
phase's demo data, see `apps/organizations/management/commands/seed_demo_data.py`).

| Username | Role | Notes |
|---|---|---|
| `northstar_owner` | Customer-org Owner (NorthStar Diagnostics) | Full org management |
| `northstar_requester_dispatcher` | Customer-org Requester/Dispatcher | Creates delivery requests |
| `riverside_owner` | Customer-org Owner (Riverside Urgent Care) | Full org management |
| `riverside_requester_dispatcher` | Customer-org Requester/Dispatcher | Creates delivery requests |
| `bkpharmacy_owner` | Customer-org Owner (Brooklyn Family Pharmacy) | Full org management |
| `ops_dispatcher` | Internal — Dispatcher | Dispatch board, assign/reassign couriers |
| `ops_manager` | Internal — Operations Manager | Cross-org read/manage, dispatch board |
| `ops_compliance` | Internal — Compliance Reviewer | Incidents console, audit log |
| `ops_courier_reviewer` | Internal — Courier Onboarding Reviewer | Courier credential review |
| `ops_finance` | Internal — Finance | Invoices, payment status |
| `ops_sysadmin` | Internal — System Administrator | Cross-org, audit log |
| `demo_courier_ana` | Courier (approved, refrigerated-capable, Manhattan) | Courier PWA at `/courier/` |
| `demo_courier_ben` | Courier (approved, ambient-only, Brooklyn) | Courier PWA |
| `demo_courier_cara` | Courier (approved; driver-license credential expiring in ~10 days) | Credential-expiration demo |
| `demo_courier_dee` | Courier (applicant, mid-onboarding, no credentials yet) | Onboarding-state demo |
| `demo_courier_eli` | Courier (suspended) | Negative-state demo |

Every other customer-org role (administrator, billing manager, compliance reviewer, read-only
auditor) also exists per organization — see `seed_demo_data`'s `CUSTOMER_ROLE_TITLES` for the full
per-org role list (username pattern: `<org-slug>_<role>`, e.g. `riverside_billing_manager`).

MFA (TOTP, Phase 8) is opt-in and **not** enrolled on any seeded account by default, so every
account above logs in with just its password.

## 4. The seeded dataset (`seed_full_demo`)

`python manage.py seed_full_demo` (`apps/organizations/management/commands/seed_full_demo.py`)
calls `seed_demo_data` first (3 organizations, 8 Manhattan/Brooklyn facilities, their users — Phase
1), then adds:

- **5 couriers with varied credential/authorization states**: a fully-approved refrigerated-capable
  courier, a fully-approved ambient-only courier, an approved courier with a credential expiring
  within `flag_expiring_credentials`'s default 30-day window, an applicant still mid-onboarding
  (no credentials/vehicle/equipment yet), and a suspended courier.
- **5 delivery requests spanning different lifecycle states**:
  1. `READY_FOR_DISPATCH` — open, unassigned, sitting in the dispatch pool.
  2. `ASSIGNED` — assigned to a courier, not yet advanced.
  3. `DELIVERED` — driven through the full real courier/custody lifecycle (pickup proof, an
     in-range temperature reading, recipient PIN verification, delivery proof) — a complete,
     genuine custody chain, not a synthetic end-state row.
  4. `INCIDENT_HOLD` — a genuine **temperature excursion**: an out-of-range reading that
     `apps.temperature.services.record_reading` itself turns into a real `SEVERE` incident, left
     open deliberately as a live item for the incidents console.
  5. `RETURNED` — a **recipient-unavailable return**, driven through a real incident +
     `initiate_return`/`complete_return` to completion.
- **1 generated invoice** (`apps.billing.services.generate_invoice_for_delivery`) for the delivered
  scenario above.

Every scenario is built by calling the same real service-layer functions the application's own
views call (`apps.deliveries.services`, `apps.dispatch.services`, `apps.couriers.services`,
`apps.custody.services`, `apps.temperature.services`, `apps.incidents.services`,
`apps.billing.services`) — never by writing rows directly, so the same state machine,
hard-eligibility gates, and custody hash chain a real user action goes through are exercised here
too. See the command's own module docstring for the full design write-up, including its documented
idempotency limitation (safe to re-run; will not "heal" a scenario a demo visitor has since changed
— use `reset_demo_data` for that).

## 5. Quota/abuse safeguards

Beyond Phase 8's per-endpoint rate limiting (recipient PIN verification/lookup — see
`docs/THREAT_MODEL.md` section 2), a genuinely public, unauthenticated-signup-adjacent demo needs
two more things: a hard cap on synthetic-data growth, and a way to reset it. Both exist as of this
phase:

### 5.1 Per-organization delivery-request cap (new this phase)

`apps.deliveries.services._enforce_delivery_request_quota`, called at the top of
`create_delivery_request` (the single creation path every delivery-request-creating view/wizard
goes through), rejects a new delivery request with a clear
`DeliveryRequestQuotaExceededError` once an organization has reached
`settings.DEMO_MAX_DELIVERY_REQUESTS_PER_ORG` existing rows (counting every status, since the
realistic abuse vector for a public demo is sheer row volume, not just open requests). Default caps:

| Settings module | Cap |
|---|---|
| `base`/`dev`/`test` (generous — not a real operational limit outside a public deployment) | 500 |
| `demo` (tightened for a genuinely public deployment) | 100 |

Both are `DEMO_MAX_DELIVERY_REQUESTS_PER_ORG` environment variables, overridable without a code
change. Tested in `apps/deliveries/tests/test_services.py`
(`test_create_delivery_request_raises_once_org_quota_is_reached`,
`test_create_delivery_request_quota_is_per_organization_not_global`,
`test_create_delivery_request_quota_check_is_a_no_op_when_setting_is_none`).

This is one concrete new safeguard, not the only imaginable one — a real deployment might also want
a per-IP/per-session cap on *organization*/*courier* creation (there is currently no public
organization-signup surface at all — see section 3 — so that specific vector does not exist yet)
and a request-body-size-based bot-abuse detector. Flagged here as reasonable future work, not
silently skipped.

### 5.2 `reset_demo_data` — cleanup/reset command (new this phase)

`python manage.py reset_demo_data --yes` deletes every row this project's own data-minimization
policy considers synthetic-only (every `@medrelay.demo` user, every organization, and everything
that cascades from either — see the command's own module docstring for the exact, dependency-safe
deletion order, since `Invoice`/`DeliveryRequest` both `PROTECT` their `Organization` FK) and
reseeds a fresh dataset via `seed_demo_data` + `seed_full_demo`. It never touches fixed
migration-seeded reference data (`CargoClass`/`TemperatureProfile`/`PricingRule`/`SLAProfile`) or a
real operator's own `createsuperuser` account (which would not have an `@medrelay.demo` email).

**No cron or scheduled-task infrastructure is wired up in this repository** — this phase
deliberately stops at "a management command a cron could call," per its own scope. An operator
running a real public deployment would point their own external cron (or their hosting platform's
scheduled-job feature, see `docs/HOSTING_OPTIONS.md`) at this command on whatever cadence they
choose; that decision belongs to the hosting choice, not to this repository.

## 6. `config/settings/demo.py`

A fourth settings module, alongside `dev`/`prod`/`test` — the module a public demo deployment would
actually set `DJANGO_SETTINGS_MODULE` to. It builds on `prod.py` (HSTS, secure cookies, SSL
redirect) and adds: a hardcoded (not env-overridable) `APP_MODE = "DEMO_MODE"`, a shorter session
cookie lifetime (12 hours, `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`) appropriate for a deployment
reachable by strangers, and the tightened `DEMO_MAX_DELIVERY_REQUESTS_PER_ORG` from section 5.1. It
does not add any new externally-reachable capability — see the module's own docstring for why "no
real external network call" is true by construction here, not something newly turned off.

No deployment currently uses this settings module (see the scope statement at the top of this
document and `docs/CURRENT_STATUS.md` "Phase 9") — it exists so the settings half of "ready for a
public demo" is complete and reviewable on its own, independent of the separate hosting-platform
decision in `docs/HOSTING_OPTIONS.md`.

## 7. No medical-operation claim — confirmed still wired everywhere

The Phase 0 disclaimer banner/context processor (`config.context_processors.app_mode`,
`templates/base.html`'s bold red `DEMO_MODE` banner) was spot-checked this phase against every
template shell added in later phases (`templates/couriers/base.html`, the anonymous
`templates/recipient/tracking.html`, `templates/registration/login.html`) — all three `{% extends
"base.html" %}`, so the banner and full disclaimer render on every page in the application,
authenticated or not, including the courier PWA and the anonymous recipient tracking page. This was
also re-verified in section 2's real HTTP walkthrough above (the literal disclaimer text was
present in the response body of an authenticated page). No template-level change was needed this
phase — Phase 8's design pass already made this indicator prominent (bold text, red background, top
of every page, before the nav).
