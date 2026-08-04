# Deploy Guide — Render (web) + Neon (Postgres)

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

**This is the project owner's own execution guide, not something performed by any automated
session.** No Render account, Neon account, or deployment was created while preparing this
document or the files it references (`render.yaml`, `config/settings/demo_render.py`,
`Dockerfile`) — see `docs/CURRENT_STATUS.md`'s dated Phase 9 hosting-decision addendum for the full
scope statement and verification performed. Everything below assumes you (the project owner) are
following it yourself, using your own email/GitHub identity.

This implements the split-services option `docs/HOSTING_OPTIONS.md` section 4 point 3 named as a
future direction: **Render** (free web-service tier, no credit card required) running the Django
`web` process, paired with **Neon** (free serverless Postgres, no credit card required, a
permanent free tier that scales to zero on idle rather than hard-expiring) for the database. The
one real, documented capability trade-off from this codebase's full local stack
(`docs/DEMO_PACKAGE.md`): no `worker`/Celery process runs on Render's free tier, so
`CELERY_TASK_ALWAYS_EAGER = True` is hardcoded in `config/settings/demo_render.py` — harmless here
because, as re-confirmed in that module's own docstring, no code in this application actually
queues a Celery task anywhere.

## 1. Create the Neon (Postgres) database

1. Go to <https://neon.tech> and sign up for a free account (no credit card required as of this
   writing — **re-verify this yourself at signup time**, free-tier terms change without notice;
   see `docs/HOSTING_OPTIONS.md`'s own repeated caveat about this).
2. Create a new **Project** (Neon's term for a database + its branches). Any region is fine; pick
   one geographically close to Render's region (`oregon`, set in `render.yaml`) if given a choice,
   for lower latency — not required for correctness.
3. On the project's dashboard, click **Connect** (or **Connection Details**). Make sure connection
   **pooling is turned on** — Neon's pooled connection string routes through PgBouncer and supports
   far more concurrent connections than a direct connection, which is the right default for a web
   app rather than a one-off script. The pooled hostname contains a `-pooler` suffix, e.g.
   `ep-cool-darkness-a1b2c3d4-pooler.us-east-2.aws.neon.tech`.
4. Copy the full connection string. It will look like:

   ```
   postgresql://<user>:<password>@<endpoint>-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require&channel_binding=require
   ```

   **`?sslmode=require` is not optional** — Neon's Postgres endpoints only accept TLS connections;
   without it (or with it stripped), the connection will fail outright. Keep the query string
   exactly as Neon gives it to you (don't drop `channel_binding=require` either).
5. This codebase's `DATABASE_URL` setting (`config/settings/base.py`, via `django-environ`'s
   `env.db_url`) expects the `postgres://` scheme, not `postgresql://` — both are accepted by
   `django-environ`, but if you want to match this repository's own convention exactly (see
   `.env.example`), you can rewrite the scheme prefix from `postgresql://` to `postgres://`;
   functionally either works.
6. **Save this connection string somewhere private for step 2.6 below.** It is a real credential
   for a real (if free and synthetic-data-only) database — never commit it to this repository.

## 2. Create the Render web service

1. Go to <https://render.com> and sign up for a free account (no credit card required as of this
   writing — **re-verify this yourself at signup time**, same caveat as step 1.1).
2. Connect your GitHub account to Render if prompted, and grant it access to the
   `mxhasan03/MedRelay` repository (either all your repositories, or that one specifically).
3. From the Render dashboard, choose **New → Blueprint**. Point it at the `mxhasan03/MedRelay`
   repository, `main` branch. Render will detect `render.yaml` at the repository root and propose
   the one service it defines (`medrelay-demo`, a free-tier Docker web service).
4. Confirm the Blueprint. Render will start a build immediately using this repository's
   `Dockerfile` — **the very first build will likely fail or the service will crash-loop**, because
   `DATABASE_URL`/`DJANGO_ALLOWED_HOSTS`/`DJANGO_CSRF_TRUSTED_ORIGINS` are marked `sync: false` in
   `render.yaml` (meaning: "the operator supplies this, don't auto-generate or guess it") and are
   not set yet. This is expected — continue to the next step rather than troubleshooting it yet.
5. Once the service exists (even in a failing/crash-looping state), open it in the Render dashboard
   and note the assigned URL, shown at the top of the service page and also under
   **Settings → Custom Domains** — it will be `https://<something>.onrender.com`, where
   `<something>` is either `medrelay-demo` or `medrelay-demo-<random-suffix>` if that exact name
   was already taken by another Render user (Render service names are globally unique).
6. Go to the service's **Environment** tab and set the following:

   | Key | Value | Notes |
   |---|---|---|
   | `DATABASE_URL` | The full Neon connection string from step 1.4 (including `?sslmode=require&channel_binding=require`) | Paste exactly as Neon gave it to you. |
   | `DJANGO_ALLOWED_HOSTS` | `<the .onrender.com domain from step 2.5, no scheme, no trailing slash>` | Example: `medrelay-demo.onrender.com`. A comma-separated list if you also plan to add a custom domain later. |
   | `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://<the same .onrender.com domain>` | **Must include the `https://` scheme** — this is a full origin, not a bare hostname, unlike `DJANGO_ALLOWED_HOSTS` above. Example: `https://medrelay-demo.onrender.com`. |

   `DJANGO_SECRET_KEY` does **not** need to be set manually — `render.yaml` marks it
   `generateValue: true`, so Render already generated and stored a random value for it the moment
   the Blueprint was created. If you ever want to rotate it, generate a fresh one yourself:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```

   and paste the result into that Environment tab (this invalidates every existing session/CSRF
   token — logged-in users will need to log in again).

7. Save the environment changes. Render will automatically trigger a new deploy (this is what
   `autoDeployTrigger: commit` plus an environment-variable change does — Render redeploys on any
   config change, not just a new commit).

## 3. What to expect on first deploy

- **Cold start delay.** Render's free web-service tier sleeps the instance after 15 minutes with
  no inbound traffic, and the *first* request after a deploy or after waking from sleep can take
  30-60+ seconds while the container starts and `dockerCommand`'s `migrate`/`seed_full_demo` steps
  run. Do not assume the deploy failed just because the first load is slow — check the **Logs** tab
  for actual errors before concluding anything is wrong.
- **Automatic static build + migration + seed, every deploy.** `render.yaml`'s `dockerCommand` runs
  `python manage.py collectstatic --noinput`, then `python manage.py migrate --noinput`, then
  `python manage.py seed_full_demo`, then starts `gunicorn` — in that order, every time the service
  deploys or restarts. The first successful run will show `collectstatic` reporting some number of
  files copied/post-processed, `migrate` applying ~50+ migrations, `seed_full_demo` reporting
  `Seeded 3 organizations, 8 facilities, ...`, and then `gunicorn`'s "Listening at..." log line.
  Every run after that will show `migrate` reporting "No migrations to apply" and `seed_full_demo`
  reporting "All Phase 9 demo scenarios already exist — nothing new to seed" — this is expected and
  correct (see `docs/CURRENT_STATUS.md` for the idempotency re-verification this relies on), not a
  sign something is being skipped incorrectly.
- **Where to find your URL.** The Render dashboard's service page header, or
  **Settings → Custom Domains** — the same `https://<something>.onrender.com` URL you already used
  to set `DJANGO_ALLOWED_HOSTS`/`DJANGO_CSRF_TRUSTED_ORIGINS` in step 2.6.
- **Health check.** Visit `https://<your-domain>.onrender.com/healthz/` — it should return
  `{"status": "ok"}` once the deploy has actually finished starting `gunicorn` (this is also the
  URL Render's own health check, `healthCheckPath` in `render.yaml`, polls to decide the deploy
  succeeded). `https://<your-domain>.onrender.com/readyz/` additionally checks database
  connectivity — if `DATABASE_URL` is wrong, this endpoint (and generally everything else) will
  fail even if `/healthz/` succeeds, since `/healthz/` is a liveness check, not a dependency check.

## 4. How to log in

Use any account from the pre-seeded demo account list in **`docs/DEMO_PACKAGE.md` section 3** —
this deploy guide does not redefine or duplicate that list (or the shared demo password) to avoid
the two documents drifting out of sync. In short: every seeded account (dispatchers, org owners,
couriers, internal ops roles) shares one synthetic password documented there, and
`northstar_owner` or `ops_dispatcher` are reasonable first accounts to try.

## 5. Known limitations to expect

- **Render free-tier cold starts.** As above — 30-60+ seconds on the first request after 15 minutes
  of inactivity. This is a real, expected UX cost of the free tier, not a bug.
- **Neon free-tier limits.** As of the research behind this deployment decision: roughly 0.5 GB of
  storage and 100 compute-hours/month, and the compute itself suspends (scales to zero) after
  ~5 minutes of inactivity, adding its own brief reconnect delay on the next query — separate from,
  and additive to, Render's own cold start. This is expected and, for a low-traffic public demo,
  fine — **re-verify these exact numbers yourself at decision/operating time**, since free-tier
  limits are exactly the kind of detail that changes without notice.
- **No live email or SMS.** Both were always mocked/local-only in this codebase (Mailpit for email
  locally, a simulated SMS event log — see `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`). Mailpit is
  not deployed here (it's a local dev/demo-only tool, not meant to be publicly reachable), so
  outbound "email" in this deployment goes nowhere visible — this changes nothing about what the
  application demonstrates, since no code path ever depended on a real inbox being reachable.
- **Celery tasks run synchronously in-process (`CELERY_TASK_ALWAYS_EAGER = True`).** This changes
  nothing observable: as documented in `config/settings/demo_render.py`'s own docstring, and
  re-confirmed by grepping this codebase immediately before writing that module, there is no
  `@shared_task`, `.delay()`, or `.apply_async()` call anywhere in `apps/` — every phase of this
  application was built as synchronous request/response code. There is no asynchronous behavior for
  this setting to change the observable timing of.
- **Shared, persistent demo data.** Every visitor uses the same pre-seeded accounts and sees the
  same underlying data (see `docs/DEMO_PACKAGE.md` section 3's own honest trade-off statement) —
  one visitor resolving the seeded temperature-excursion incident, for instance, is visible to the
  next. `python manage.py reset_demo_data --yes` (run manually from Render's **Shell** tab, if
  available on your plan, or not at all if the free tier doesn't offer shell access — re-verify
  this at operating time) is the existing mitigation; no scheduled/cron reset is wired up by this
  deployment.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Deploy log shows `django.core.exceptions.ImproperlyConfigured` or a `KeyError`/`ValueError` mentioning an env var name | A required environment variable (most likely `DATABASE_URL`) is missing or empty | Re-check the **Environment** tab against step 2.6's table — an unset `sync: false` var is blank, not absent, until you fill it in |
| Deploy log shows `OperationalError: connection to server ... failed` or an SSL-related Postgres error | `DATABASE_URL` is wrong, missing `?sslmode=require`, or points at a suspended/deleted Neon project | Re-copy the connection string fresh from Neon's **Connect** dialog (step 1.3-1.4); confirm the Neon project still exists and its compute isn't paused in a way that blocks new connections |
| Migration step fails partway with a Postgres-specific error | Usually a stale/partially-migrated database from a previous failed attempt | In Neon's SQL editor, you can drop and recreate the database (there is no real data to lose in a fresh demo deployment) and let the next deploy's `migrate` step start clean |
| Site loads but every login/POST returns `403 Forbidden` mentioning CSRF | `DJANGO_CSRF_TRUSTED_ORIGINS` is unset, or set to the wrong domain, or missing the `https://` scheme | Set it to exactly `https://<your-actual-assigned-domain>.onrender.com` (step 2.6) — it must be the literal domain Render assigned you, not a guessed name, and must include the scheme |
| CSS/JS/static assets 404, or the page loads with no styling | `collectstatic` didn't complete — `render.yaml`'s `dockerCommand` runs it as the first step, but a build/deploy that failed or was interrupted before that step finished would leave WhiteNoise's `CompressedManifestStaticFilesStorage` with no manifest to serve from | Check the deploy logs for a `collectstatic` failure specifically (it runs first, before `migrate`/`seed_full_demo`/`gunicorn`) and re-deploy; `collectstatic` itself doesn't need a working `DATABASE_URL` to succeed, so if it's failing, the cause is almost always a `DJANGO_SETTINGS_MODULE`/static-file-source problem, not a database problem |
| `Bad Gateway` / `502` shown by Render, with no obvious app-level error in logs | The container isn't listening on the `$PORT` Render assigned | Confirm `dockerCommand` in `render.yaml` still ends in `--bind 0.0.0.0:$PORT` (not a hardcoded port) — this was verified working in this session with `docker run -e PORT=10000 ...`, so a regression here means `render.yaml` or the `Dockerfile` was edited since |
| Everything looks fine but the first page view after a while is very slow | Render free-tier cold start (and/or Neon compute waking from scale-to-zero) | Expected — see section 5. Refresh once the page finally loads; subsequent requests within the active window are fast |

## 7. Everything this guide does *not* do

Per this repository's own governance (`CLAUDE.md`, `docs/SECURITY_COMPLIANCE_BOUNDARIES.md`) and
the scope boundary every prior phase document has repeated: following this guide stands up a
**public demo of a synthetic-data-only software prototype**. It does not, and cannot, authorize or
constitute a real pilot, real PHI handling, real payments, or any claim of HIPAA/OSHA/DOT/pharmacy/
employment/legal compliance — see `docs/PILOT_READINESS/GO_NO_GO_REPORT.md` for what an actual
pilot decision would require, entirely separate from and unaffected by this deployment.
