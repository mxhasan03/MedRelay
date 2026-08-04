"""Demo-deployment settings — Phase 9 ("Free public demonstration option").

Distinct from `dev`/`prod`/`test`: this is the settings module a public demo
deployment of MedRelay — *whenever and wherever the project owner decides to
run one, see `docs/HOSTING_OPTIONS.md`* — would actually use. No such
deployment has been performed as part of this phase (see
`docs/CURRENT_STATUS.md` "Phase 9" and the explicit scope boundary at the
top of that section); this module exists so the *settings* half of "ready to
deploy" is complete and reviewable independent of the deploy decision
itself.

This module builds on `prod.py` (which already sets `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`, and HSTS) rather than
duplicating those — a public demo deployment needs everything `prod.py`
already hardens, plus the demo-specific choices below. It does **not** add
any new externally-reachable capability: no payment/SMS/maps/analytics/error-
tracking SaaS is wired in here or anywhere else in this codebase (see
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`); `EMAIL_HOST`/`CELERY_BROKER_URL`/
`DATABASE_URL` remain plain environment variables the operator points at
their own local/self-hosted Mailpit, Valkey, and PostgreSQL — exactly the
same adapters the local Docker demo package uses (see
`docs/DEMO_PACKAGE.md`). "Disables any real external network calls" is true
by construction (nothing in `apps/`+`config/` ever makes one — enforced by
`python manage.py audit_cost`'s prohibited-service-indicator scan), not
something this settings module has to newly turn off.
"""

from .prod import *  # noqa: F403
from .prod import env

# Hardcoded, not env-overridable. `base.py`'s APP_MODE is an env-overridable
# *default* of "DEMO_MODE" (so a stray/missing environment variable still
# does the safe thing), but this module is inherently a demo deployment —
# leaving APP_MODE env-controlled here would let a misconfigured environment
# variable silently claim a non-demo mode this codebase does not support
# (see CLAUDE.md "Operating mode: DEMO_MODE only" — PILOT_MODE is not
# implemented and must never be reachable by an environment-variable typo).
APP_MODE = "DEMO_MODE"

DEBUG = False

# --------------------------------------------------------------------------
# Conservative session/cookie posture for a deployment reachable by
# strangers on the public internet. Django's defaults (2-week session
# cookie, HttpOnly on) are reasonable for an authenticated B2B portal behind
# a login wall, but a free public demo is explicitly inviting exploration by
# people who were never vetted the way a real customer onboarding would —
# shorter sessions reduce the value of a stolen/left-open session.
# CSRF_COOKIE_HTTPONLY is deliberately left at Django's default (False):
# static/js/courier.js and static/js/offline-queue.js read the `csrftoken`
# cookie directly to attach an `X-CSRFToken` header on the courier PWA's
# JSON fetch calls (see apps/couriers/views.py) — setting this True would
# break that real, tested mechanism, not just a hypothetical one.
# --------------------------------------------------------------------------
SESSION_COOKIE_AGE = env.int("DEMO_SESSION_COOKIE_AGE", default=12 * 60 * 60)  # 12 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# --------------------------------------------------------------------------
# Tighter demo quota than base.py's generous default (500) — a genuinely
# public deployment should hit its per-organization cap well before
# accumulating enough rows to matter for resource use. See
# apps.deliveries.services._enforce_delivery_request_quota and
# docs/DEMO_PACKAGE.md "Quota/abuse safeguards". Still generous relative to
# `seed_full_demo`'s own handful of seeded rows per organization.
# --------------------------------------------------------------------------
DEMO_MAX_DELIVERY_REQUESTS_PER_ORG = env.int("DEMO_MAX_DELIVERY_REQUESTS_PER_ORG", default=100)
