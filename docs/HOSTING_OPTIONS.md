# Hosting Options — Phase 9 Recommendation Document

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

**This document is a recommendation, not a decision or an action.** No hosting account was
created, no platform was selected, and nothing was deployed anywhere as part of producing this
document — per `docs/IMPLEMENTATION_ROADMAP.md` Phase 9's own text ("Do not select a hosting
platform that requires payment or a credit card without owner approval") and this session's explicit
scope boundary (see `docs/CURRENT_STATUS.md` "Phase 9"). **Actual platform selection and deployment
is the project owner's decision, to be made separately from this session, whenever (if ever) it is
wanted.** Everything below is research and a recommendation for that future decision, not
preparation to act on it unilaterally.

Free-tier terms for every platform named below change frequently and without notice — this is
inherent to "free tier" as a product category, not a gap in this research. Anything not personally,
freshly re-verified against the platform's current published terms **at decision time** should be
treated as potentially stale, regardless of how it reads here. Claims below are marked "needs
verification at decision time" wherever the underlying fact is exactly the kind that free-tier
providers change often (limits, whether a card is required, whether a service sleeps).

## 1. What this application actually needs to run for real

From `compose.yaml`/`Dockerfile` (the authoritative description of the stack, see
`docs/DEMO_PACKAGE.md`):

- A Python/Django process (`web`) — needs to stay running to serve requests; a "sleeps after
  inactivity, cold-starts on the next request" free-tier pattern is a real UX cost (10-60+ second
  first-load delay) but not a correctness blocker for a demo.
- A **background worker process** (`worker`, Celery) — this is the single hardest requirement to
  satisfy on a free tier. Many free web-hosting tiers run exactly one process type (the web
  process) and either don't support a second always-running process at all, or only as a paid
  add-on. Check this specifically for any candidate — "free tier" often means "free *web service*
  tier," silently excluding background workers.
- **PostgreSQL** (ideally with PostGIS, though this codebase does not yet use PostGIS-specific
  queries — see `docs/CURRENT_STATUS.md` Phase 1 design decision #1 — so a plain-Postgres free tier
  would work today) with **persistent storage** — a free database that resets/expires after a fixed
  number of days is a real operational problem for a "demo that stays up," not just an inconvenience.
- **Valkey/Redis** (cache + Celery broker) — needs to be reachable by both `web` and `worker`;
  a free tier that only offers Redis as a paid add-on, or that imposes a very low
  connection-count/command-rate limit, is a real constraint.
- **Outbound email capture (Mailpit)** — this is a *local-only* dev/demo tool
  (`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`); a public deployment either keeps it as an internal,
  non-public service (fine — nothing about mail needs to be internet-reachable) or the demo simply
  ships without a visible "sent mail" viewer, since a real outbound email provider is explicitly out
  of the zero-cost policy's allowed list.
- **No required credit card or payment method** — the hard constraint from the roadmap and this
  session's scope boundary.

## 2. Candidate free-tier platforms

None of these were signed up for, configured, or deployed to in this session. Everything below is
research/recollection, not a live-verified account.

| Platform | Web process (free) | Background worker (free) | Postgres (free) | Redis/Valkey (free) | No card required | Notable free-tier catches |
|---|---|---|---|---|---|---|
| **Render** | Yes — sleeps after inactivity on free web services | Not available free (background workers are a paid service type) | Historically offered a free Postgres instance that **expired after a fixed window** (commonly cited as ~30-90 days) rather than persisting indefinitely | Not available free as a persistent add-on | Needs verification at decision time (free tier terms and card requirements here have changed more than once) | Worker + persistent DB are the two blockers for this app specifically |
| **Fly.io** | Needs verification at decision time — Fly's free allowance has changed materially over time and, as of recent history, generally requires a card on file even for usage within a free allowance | Same card-on-file caveat would apply to any second Fly "machine" running the worker | Fly Postgres is self-managed on your own allocated compute, so it's "free" only insofar as the underlying compute allowance is | No managed free Redis; would need self-hosting on the same compute | Needs verification — treat as likely requiring a card | If a card is required at all, this fails the roadmap's hard constraint regardless of usage cost |
| **Railway** | Historically offered a small free/trial credit, not an indefinite free tier | Same credit model would apply | Same credit model | Same credit model | Needs verification — Railway's free offering has shifted from "free tier" to "trial credit that expires" more than once | A time-limited trial credit is a materially different (weaker) offer than a durable free tier; re-check the current model specifically before assuming it qualifies |
| **PythonAnywhere** | Yes — a genuinely durable always-on free web app tier for Python (no sleep-on-inactivity for the free web app itself, unlike most competitors) | Free tier does not include always-on scheduled/background tasks (a paid-plan feature) | Free tier historically ships MySQL, not PostgreSQL, and with modest storage; no PostGIS | No managed Redis on the free tier | Historically no card required for the free tier (needs verification) | Wrong database engine (MySQL, not Postgres) and no worker support are both real blockers for this app as built, not just inconveniences |
| **Oracle Cloud "Always Free"** | Yes, via a real persistent Compute VM (notably including an ARM Ampere shape with a meaningful free CPU/RAM allowance) capable of running `docker compose` directly, unlike PaaS free tiers | Yes — it's a real VM, so `worker` runs exactly as it does locally | Yes, if self-hosted in the same `docker compose` stack (or via Oracle's free-tier managed DB service, smaller allowance) | Yes, self-hosted in the same stack | **No** — Oracle, like every other major cloud provider's "always free" VM tier, requires a credit card at account creation for identity verification, even though the always-free resources themselves are not billed absent an explicit upgrade | The identity-verification card requirement is the specific, well-known catch that should disqualify this under the roadmap's literal "no... credit card without owner approval" wording, even though the compute itself would otherwise be the best technical fit of anything in this table |
| **Google Cloud / AWS / Azure "free tier" VMs** | Technically yes (a small always-free/12-months-free VM instance) | Yes, on the same VM | Yes, self-hosted | Yes, self-hosted | **No** — all three require a credit card at signup | Same disqualifying catch as Oracle above, for the same reason |
| **A community/friend's own server, or the project owner's own always-on hardware** | N/A (not a "platform") | N/A | N/A | N/A | Yes, if truly no payment is involved | Not really "hosting research" so much as "does the owner have a machine to run this on" — worth naming as an option since it trivially satisfies every technical requirement and the no-card constraint, at the cost of not being a polished, professional demo URL |

### Reading the table honestly

**Every PaaS-style "free web app" tier surveyed above fails at least one hard requirement** for
this specific stack — most commonly the background-worker requirement, the persistent-Postgres
requirement, or (for the platforms whose free tier *would* otherwise fit) the no-credit-card
requirement. This is not a surprising result: this application was built to the zero-cost policy's
*local self-hosting* model (`docker compose`, a real Postgres/Valkey/Mailpit stack, own worker
process) from Phase 0 onward, which is a poor match for "single free web dyno" PaaS products that
are increasingly the *only* thing offered for free by platforms that used to offer more. The
platforms that technically satisfy the compute/worker/database requirements (Oracle/AWS/GCP's
always-free VM tiers) all share the same card-at-signup catch, which is disqualifying under the
roadmap's literal wording regardless of whether any charge would ever actually occur.

## 3. The roadmap's own explicitly-allowed alternative

`docs/IMPLEMENTATION_ROADMAP.md` Phase 9 explicitly names an alternative to weakening the system to
fit a free host: **"publish a local-run package, screenshots/video, and static marketing/demo site
instead of weakening the system."** Concretely, this repository is already most of the way there:

- **The local-run package** (`docker compose up --build` + `seed_full_demo`) is real, documented,
  and was actually re-verified end-to-end in this session against the genuine PostGIS/Postgres image
  — see `docs/DEMO_PACKAGE.md`. A prospective employer/reviewer with Docker installed can be up and
  looking at a fully-seeded, role-based demo in a few minutes, with zero hosting decision required
  of anyone.
- **Screenshots/a short screen-recorded video** walking through the pre-seeded accounts (dispatcher
  assigning a courier, the incidents console showing the seeded temperature excursion, the recipient
  tracking page) would be genuinely free, permanent (no host to keep paying for/maintaining), and
  representative — no code in this repository would need to change to produce this; it is out of
  scope for this session (no video/screenshot tooling was requested or used here) but is flagged as
  the lowest-friction, zero-infrastructure option available right now.
- **A static marketing/demo page** (e.g. a single HTML page — itself trivially free to host almost
  anywhere, including a static-pages host with no server component and thus none of this table's
  worker/database problems) describing the project and linking to the local-run package/repo/video
  is the natural complement.

## 4. Recommendation

1. **Do not select any of the PaaS free tiers in section 2 as-is** — every one of them either fails
   a hard technical requirement (worker, persistent Postgres, or correct DB engine) or fails the
   roadmap's no-required-card constraint. None of them are a clean fit without either weakening the
   system (dropping Celery/the worker, moving off Postgres, accepting a non-persistent database) or
   accepting a card requirement the roadmap explicitly says not to accept without the owner's
   sign-off.
2. **Lead with the local-run package + screenshots/video + a static marketing page** (section 3) as
   the actual Phase 9 deliverable for demonstrating this project publicly, since it needs no
   platform decision at all and has zero ongoing cost or maintenance burden.
3. **If a genuinely public, click-a-link demo is wanted anyway**, the two most promising directions
   to re-research *at decision time* (not now) are, in order:
   - A **split-services** approach: a managed free-tier Postgres specifically built for exactly this
     use case (e.g. a serverless Postgres provider with a genuinely free, no-card tier and
     auto-suspend-on-inactivity rather than a hard expiry — needs verification, as this is exactly
     the kind of offering that changes fastest) paired with a free web-process host for `web` only,
     and **accepting that `worker`/Celery would need to be dropped or run in-process
     (`CELERY_TASK_ALWAYS_EAGER=True`, already an existing, tested settings knob — see
     `config/settings/test.py`) for the public demo specifically** — a real, documented,
     capability-reducing trade-off, not a silent one, and a decision the owner should make
     knowingly rather than have made for them.
   - A **single always-on VM** (Oracle/AWS/GCP always-free tier, or the project owner's own
     hardware) running the exact same `docker compose` stack as the local package, accepting the
     card-at-signup requirement as a known, explicit exception the owner affirmatively approves
     (the roadmap's own escape hatch is "without owner approval," not "under no circumstances") —
     this is the only option in this document that requires **zero** functional compromise to the
     application as built.
4. **Either way, this is the project owner's decision, not this session's** — per the scope boundary
   stated at the top of `docs/CURRENT_STATUS.md` "Phase 9" and repeated here: no account should be
   created and no deployment should be performed until the owner has reviewed this document and
   explicitly chosen a direction (including "none — ship the local package instead," which is this
   document's own lead recommendation).
