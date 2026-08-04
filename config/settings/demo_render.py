r"""Render-specific demo-deployment settings.

This is the settings module a **Render** (free web-service tier) + **Neon**
(free serverless Postgres) split-services public demo deployment actually
sets `DJANGO_SETTINGS_MODULE` to. It builds on `config/settings/demo.py`
(hardcoded `APP_MODE = "DEMO_MODE"`, `prod.py`'s HSTS/secure-cookie/SSL-
redirect hardening, the tightened `DEMO_MAX_DELIVERY_REQUESTS_PER_ORG`)
rather than duplicating any of that — everything below is genuinely specific
to *this one platform pairing*, not a second copy of `demo.py`. See
`docs/DEPLOY_RENDER_NEON.md` for the operator-facing deployment guide and
`docs/HOSTING_OPTIONS.md` section 4 point 3 for the decision this module
implements. As with `demo.py`, no deployment using this module has been
performed by any automated session that authored it — see
`docs/CURRENT_STATUS.md`'s dated Phase 9 hosting-decision addendum.

Three real, deliberate deltas from `demo.py`, each documented here because
each is a load-bearing correctness fix or a genuine capability trade-off,
not a stylistic choice:

1. **`SECURE_PROXY_SSL_HEADER`.** Render terminates TLS at its own edge
   proxy and forwards every request to this application over plain HTTP,
   adding an `X-Forwarded-Proto: https` header. `prod.py` sets
   `SECURE_SSL_REDIRECT = True` by default, and Django's own SSL-redirect
   logic only ever looks at `request.is_secure()` — which, without this
   setting, is always `False` behind a reverse proxy, since Django only
   trusts the proxy header once told which header/value combination to
   trust. The result without this line is not "insecure" but *totally
   broken*: every request looks insecure to Django, gets redirected to
   `https://...`, arrives back at the same proxy over the same plain-HTTP
   connection, and looks insecure again — an infinite redirect loop, not a
   security gap. This is Render's documented proxy behavior (shared by
   effectively every PaaS host that terminates TLS at an edge load
   balancer), not something specific to a misconfiguration on our end.

2. **`CACHES` overridden to `LocMemCache`.** `base.py`'s default `CACHES`
   points at a Redis-protocol backend (`REDIS_URL`, Valkey locally) because
   a real multi-process/multi-host deployment needs a *shared* cache for
   `django-ratelimit` (`RATELIMIT_USE_CACHE = "default"`,
   `apps/recipient/views.py`'s PIN-verification rate limit) to mean
   anything. Render's free web-service tier is a single instance running a
   single `gunicorn` worker process (see `render.yaml` — no `--workers`
   flag is passed, so gunicorn defaults to exactly one sync worker), so
   there is no second process for a shared cache to actually synchronize
   with; a in-process `LocMemCache` *is* the shared cache for this
   deployment's entire request-handling capacity. Standing up a third
   free-tier external service (a hosted Redis/Valkey instance) purely to
   satisfy django-ratelimit's cache-backend requirement would add a new
   account, a new free-tier expiry/limit surface, and a new failure mode
   (rate limiting silently breaking if that external cache is unreachable)
   for zero real benefit at this scale — this deployment already isn't
   using Celery as a real task queue either (see point 3), so `REDIS_URL`
   has no live consumer left once this override is in place. If Render's
   free tier is ever changed to run multiple worker processes or replicas,
   this override must be revisited (see `SILENCED_SYSTEM_CHECKS` below,
   which documents exactly that trade-off the same way
   `config/settings/test.py` already does for the same reason).
   `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` are deliberately left
   pointing at `REDIS_URL`'s default (unreachable on Render, since no
   Valkey/Redis service is provisioned there) rather than being rewired —
   harmless, because point 3 below means Celery never actually opens a
   broker connection in this deployment.

3. **`CELERY_TASK_ALWAYS_EAGER = True`, hardcoded (not env-toggled).**
   Re-confirmed immediately before writing this module (`grep -rn
   "shared_task\|\.delay(\|apply_async" apps/ config/` — zero matches) that
   there is still, as of this deployment decision, no `@shared_task`,
   `.delay()`, or `.apply_async()` call anywhere in this codebase. Every
   phase from 1 through 10 was built entirely as synchronous Django
   request/response code and management commands; `compose.yaml`'s
   `worker` service exists for architectural completeness (matching
   `docs/ARCHITECTURE_AND_DATA_MODEL.md`'s eventual async-task story) but
   has never had an actual task to run. Setting this here is therefore
   **correctness-neutral, not a capability cut**: there is no
   asynchronous behavior for a Render deployment (which has no `worker`
   process at all — see `docs/HOSTING_OPTIONS.md` section 1, "a free tier
   often means a free *web service* tier only") to silently fail to run.
   Hardcoding it (rather than leaving it env-toggled like `base.py`'s
   default) reflects that this is inherent to *this* deployment shape, not
   an environment-variable-typo-away accident — the same reasoning
   `demo.py` already applies to its own hardcoded `APP_MODE`.
"""

from __future__ import annotations

from .demo import *  # noqa: F403
from .demo import env

# --------------------------------------------------------------------------
# 1. Trust Render's edge-proxy TLS termination. See module docstring point 1.
# --------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --------------------------------------------------------------------------
# 2. Single-instance, single-worker cache. See module docstring point 2.
# --------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "medrelay-render-demo-cache",
    }
}

# django-ratelimit's system checks (django_ratelimit.E003/W001) flag
# LocMemCache as "not a shared cache" — true in a real multi-process/
# multi-host deployment, false here, for the exact reason given in the
# module docstring (single free instance, single gunicorn worker). Same
# silencing `config/settings/test.py` already does for the identical
# single-process reason; kept honestly out of `demo.py`/`prod.py`, which
# still use the real shared Valkey/Redis-protocol cache.
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003", "django_ratelimit.W001"]

# --------------------------------------------------------------------------
# 3. No real background-task infrastructure on this platform. See module
#    docstring point 3.
# --------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = True

# --------------------------------------------------------------------------
# CSRF: Django's CSRF protection requires the exact scheme+domain of any
# cross-origin-looking POST request to be listed in CSRF_TRUSTED_ORIGINS
# (not just ALLOWED_HOSTS) once the request arrives via a reverse proxy on
# HTTPS, which is exactly Render's setup (see point 1 above). Not previously
# defined anywhere in this settings package (base/dev/prod/test/demo) — this
# is the first module that actually needs it, since it's the first module
# meant to run behind Render's proxy. Empty by default (matching
# ALLOWED_HOSTS' own env-driven, no-default-value pattern in base.py); the
# operator MUST set DJANGO_CSRF_TRUSTED_ORIGINS to
# "https://<their-service-name>.onrender.com" once Render has assigned that
# service its subdomain (see docs/DEPLOY_RENDER_NEON.md step 2) — every
# authenticated POST in this application (login, delivery-request creation,
# dispatch assignment, custody capture, etc.) will otherwise fail CSRF
# validation with a 403 on first deploy.
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
