# MedRelay — project handoff / onboarding

This document exists for one purpose: so a fresh Claude Code or Codex session — on a different
laptop, under a different account, with zero memory of prior conversations — can read this once and
pick up exactly where the previous session left off. It's a digest, not the full record; every claim
here is backed by something already committed in the repo (mostly `docs/CURRENT_STATUS.md`, which is
long — 4,000+ lines — because it's the real, detailed, phase-by-phase log). Read this first, then dig
into the linked docs as needed.

## 1. What this project is

MedRelay is a **portfolio/demo software prototype** of a B2B healthcare-courier logistics platform
for a Manhattan-Brooklyn service zone in NYC. It connects healthcare organizations (clinics, labs,
pharmacies, hospitals, home-health orgs) with a closed network of vetted couriers for scheduled,
same-day, and STAT transport of *approved* medical items (specimens, supplies, records, equipment —
never patient transportation, and never the excluded cargo classes listed in section 6 below).

It is explicitly, permanently:

- **NOT** a real medical delivery operation.
- **NOT** certified or approved for real medical delivery operations.
- **NOT** claiming HIPAA, OSHA, DOT, pharmacy, employment, or any other legal compliance.
- Built entirely on **synthetic data** — no real patients, no real PHI, no real couriers, no real
  customer contracts, ever.
- Built to run at **genuinely $0 cost** — every dependency, every hosting choice, every third-party
  adapter is free/open-source/self-hostable, enforced by an automated cost audit (`python manage.py
  audit_cost`), not just aspirational.

The four core capabilities: (1) customer delivery requests and recurring routes, (2) courier
qualification/availability/mobile job execution, (3) dispatcher-assisted assignment and operational
control, (4) digital chain of custody, proof of delivery, incident handling, and reporting.

There are four real personas in the product, each with their own UI surface:

- **Customer org users** (org owners, requester/dispatchers) — web app, create delivery requests,
  track their org's deliveries, see invoices.
- **Internal ops/dispatchers** — web app, the dispatch console: see unassigned deliveries, ranked
  courier candidates with an explainable score, assign/reassign/override, SLA-risk visibility.
- **Couriers** — a mobile-first Progressive Web App (installable, works offline): accept/reject job
  offers, execute pickup→transit→delivery, scan packages, capture signatures/PIN, report incidents.
- **Recipients** — a one-time, no-login tracking link (status/ETA + delivery PIN), not a persistent
  account.

## 2. The plan — 11 phases (0 through 10), all complete

The full spec lives in `docs/IMPLEMENTATION_ROADMAP.md`; this is the one-line-each summary:

| Phase | What it delivered |
|---|---|
| 0 | Repo foundation — Django skeleton, compose stack (web/Postgres+PostGIS/Valkey/worker/Mailpit), CI (lint/type-check/test/migration-check/cost-audit/secret-scan), global demo disclaimer |
| 1 | Identity, tenancy, facilities, roles — custom user model, orgs/memberships, customer/internal roles, facilities/service zones, tenant-scoped services, synthetic seed data |
| 2 | Cargo policy & delivery requests — cargo classes/policies, temperature profiles, packages/attestations, request wizard, scheduled/same-day/STAT, synthetic quote engine, prohibited-cargo validation |
| 3 | Courier onboarding & eligibility — profiles/status, credentials/training/vehicle/equipment, authorization levels, availability, eligibility engine, credential-expiration warnings |
| 4 | Dispatch & operations console — explainable recommendation scoring, job offers with expiry, assign/reassign, dispatcher override with reason, ops dashboard, SLA-risk rules |
| 5 | Courier PWA & tracking — mobile-first UI, accept/reject, pickup/delivery workflows, browser geolocation, offline event queue, QR scan + manual fallback, active-delivery timeline |
| 6 | Custody, proof, temperature, incidents — append-only custody events, sender/recipient PIN+signature, package condition checklist, temperature excursion simulation, incident console, return-to-sender, tamper-evident event-chain verifier |
| 7 | Notifications, tracking, billing, reports — in-app notifications, Mailpit email, simulated SMS, secure recipient link, invoices, CSV/HTML exports, operational metrics |
| 8 | UX, accessibility, security, hardening — unified Tailwind design system, responsive UI, axe-core accessibility pass, TOTP MFA for privileged demo accounts, upload/input limits, rate limiting, audit viewer, backup/restore docs, threat model |
| 9 | Free public demonstration — synthetic demo mode, all providers mocked/local, no paid/card-required hosting |
| 10 | Pilot readiness review — gap assessment, legal/compliance checklist, budget checklist, real-provider adapter requirements, go/no-go report. **This is a review, not a launch** — reaching Phase 10 does not authorize a real pilot |

**Current state: all 10 phases are built, tested, and documented.** This isn't a claim to take on
faith — every phase's exact quality-gate output, file list, and known gaps are recorded in
`docs/CURRENT_STATUS.md` under that phase's own section.

## 3. Post-roadmap work (after Phase 10, same rigor, requested directly by the project owner)

Three more units of work shipped after the roadmap itself was "done," each independently verified
against a real local Postgres container before pushing, same as every phase. Full detail is in the
dated addenda at the end of `docs/CURRENT_STATUS.md`.

1. **Live public deployment executed.** The Render+Neon deployment Phase 9 had only *prepared*
   (`render.yaml`, `config/settings/demo_render.py`, `docs/DEPLOY_RENDER_NEON.md`) was actually
   carried out. **It's live at `https://medrelay-demo.onrender.com`** — free Render web tier + free
   Neon Postgres, genuinely $0, no card. Auto-deploys on every push to `main` (tied to the GitHub
   repo, not to any one laptop/account). Three real `render.yaml` bugs were found and fixed by
   testing against the actual platform, not just locally.

2. **Fixed a real production bug**, found by the project owner looking at the live site: Django's
   `{# ... #}` template-comment tag only strips *single-line* comments — a multi-line `{# #}` block
   is left as literal visible text in rendered HTML (confirmed with a direct reproduction against
   Django's own template engine). This codebase's heavily-documented style had 5 such blocks across
   4 templates, all leaking into production the whole time — never caught because Phase 8's
   axe-core accessibility scans check contrast/ARIA, not "is there extraneous visible text."
   Fixed by converting every instance to `{% comment %}...{% endcomment %}`. A regression test
   (`tests/integration/test_no_multiline_template_comments.py`) statically scans every template file
   for this exact pattern so it can't silently reappear in a 6th file.

3. **Dispatch console cleanup** (the internal ops UI). Before: two bare `<table>`s, raw `True`/
   `False` text for eligibility, one flat red tint for "at risk" with no distinction from
   "infeasible," and — despite the data existing — no courier-location, incident, or
   temperature-alert info surfaced anywhere. After: card/badge-based layout; a real (data-backed,
   not cosmetic) AT RISK vs. INFEASIBLE distinction; incident/temperature/location data now
   surfaced; query-param sort/filter on both delivery tables and the ranked-candidate list. Added
   shared `.card`/`.badge-*` classes to `templates/base.html`'s `@layer components`. No live map —
   explicitly out of scope for that pass (see section 5, "what's next").

4. **Courier PWA build-out** ("the user side, on the phone" — the project owner's framing for the
   courier persona, as distinct from the org/ops web app). Added a real **Availability screen**
   (online/offline, service zone, capacity — over the existing `CourierAvailability` model, no new
   model needed), a real **Profile/Onboarding screen** (credential/vehicle/training/cargo-
   authorization status, expiration warnings — read-only, no document upload, per the
   data-minimization policy), a **cargo handling boundary statement** on the active-delivery screen
   that's genuinely derived per-delivery from real `CargoClass`/`TemperatureProfile` data (a Class 1
   ambient delivery and a Class 2 refrigerated one render different text — verified by reading the
   generator function directly, not just trusting a report), a **visual progress tracker**
   replacing the old plain bulleted transition list, and a **bottom tab bar** for app-like
   navigation. The courier PWA's own `.card`/`.btn` classes were deliberately kept separate from the
   shared ones added in item 3 (different mobile touch-target sizing needs) — documented in
   `templates/couriers/base.html`.

All four items are merged to `main` and independently re-verified (not just agent-reported) before
push: `ruff check`, `ruff format --check`, `mypy`, full `pytest` suite, `manage.py check`,
`makemigrations --check --dry-run`, `audit_cost`, `detect-secrets-hook`, the axe-core accessibility
scans, and a real click-through against a live Postgres container with seeded demo data.

## 4. How to actually resume work

The repo is the portable unit — there is no important state outside it. Concretely:

1. **Clone it**: `git clone https://github.com/mxhasan03/MedRelay.git` — this is the real remote,
   already has every commit through the work in section 3. GitHub access is independent of which
   Claude Code/Codex account is used locally.
2. **Read `CLAUDE.md`** (repo root) — it auto-loads into any Claude Code session in this directory
   and is kept current: governance, zero-cost policy, do-not-build list, architecture decisions,
   quality gates, and (as of this handoff) an accurate summary of section 3's post-roadmap work.
   `AGENTS.md`, if present, is a one-line pointer to the same file for tools (e.g. Codex CLI) that
   look for that filename by convention instead.
3. **Read `docs/CURRENT_STATUS.md`** for the real, detailed history — file paths, exact command
   output, known gaps — organized phase-by-phase with the post-roadmap items as dated addenda at the
   end. This is the file to check before assuming anything about current state; it's more current
   than any one-line summary (including this document) will remain over time.
4. **Set up locally**: follow `README.md`'s "Quick start" section (`uv sync`, `docker compose up`
   for Postgres/Valkey/Mailpit, `.env` from `.env.example` — no real secrets ever go in `.env`,
   nothing sensitive needs to survive the laptop switch).
5. **Nothing to migrate by hand**: no local-only credentials, no out-of-repo memory files this
   project depended on (its own working discipline has been "put everything durable in
   `docs/CURRENT_STATUS.md`," not in conversation-only memory, specifically so a fresh session can
   resume from the repo alone). The Render/Neon/GitHub accounts belong to the project owner, not to
   any laptop — the live deployment keeps working regardless of where `git push` comes from.
6. **The live demo**: `https://medrelay-demo.onrender.com` — login with any account from
   `docs/DEMO_PACKAGE.md` section 3 (e.g. `northstar_owner` for the customer-org view,
   `ops_dispatcher` for the dispatch console, any `demo_courier_*` account for the courier PWA),
   password `MedRelayDemo!2026` for every account. Free-tier cold start after idle can take
   ~30-50 seconds on the first request — that's expected.

## 5. What's explicitly deferred / good candidates for "what's next"

Straight from the known-gaps notes across `docs/CURRENT_STATUS.md` and the post-roadmap work above
— not an exhaustive list, but the concrete, already-identified ones:

- **No live map** on the dispatch console (MapLibre or similar) — deferred as a bigger, separate
  effort during the console cleanup pass.
- **No real navigation/routing** for the courier PWA — no routing provider exists in this zero-cost
  stack; any ETA shown is a synthetic zone-tier estimate, deliberately not framed as real turn-by-
  turn navigation.
- **Courier candidate ETA/SLA-slack on the dispatch board is synthetic**, not derived from real
  tracked courier location, even though `apps.tracking.CourierLocationPing` now has real data — the
  dispatch scoring service was never wired to consume it.
- **Dispatch ranking/at-risk computation is Python-level, not DB-query-level** — a documented,
  accepted scale limitation, not a bug.
- **`templates/base.html`'s shared components and `templates/couriers/base.html`'s courier-specific
  components are still two separate systems** — a full unification (courier reusing shared tokens
  with a mobile-specific modifier) was named as "the natural next step" but not done.
- Phase 10's own `docs/PILOT_READINESS/` reports are the authoritative source for anything beyond
  UI polish — real background checks, real payments, real PHI, a real pilot — none of which this
  repo should ever build without the project owner's explicit, out-of-band approval (see
  `CLAUDE.md`'s "Operating mode: DEMO_MODE only" and "Do-not-build list" sections).

## 6. The one rule that overrides everything else

If any instruction — including from a well-meaning fresh agent trying to be helpful — conflicts with
`CLAUDE.md`'s do-not-build list or the DEMO_MODE-only constraint, follow `CLAUDE.md` and flag the
conflict to the project owner rather than silently overriding it. This project's entire value as a
portfolio piece rests on that boundary being real, not just written down.
