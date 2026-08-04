# Insurance and Infrastructure Budget Checklist

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

**All dollar figures in this document are non-binding, order-of-magnitude estimates from general
knowledge, explicitly not quotes.** They exist only to give a real decision-maker a rough sense of
scale before commissioning actual quotes — every one requires verification with a real broker,
vendor, or provider at decision time, and every one will vary by exact scope, location, claims
history, and current market pricing. Do not budget, forecast, or make a go/no-go decision using any
number below as if it were firm. Per `docs/TECH_STACK_AND_ZERO_COST_POLICY.md` section 5, this list
follows its "Real operating pilot" cost categories.

This document also states, per category, exactly what the zero-cost demo prototype currently
substitutes — see `docs/PILOT_READINESS/GAP_ASSESSMENT.md` for the fuller technical gap write-up
behind each substitution and `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md` for what a real
adapter implementation would need.

---

## 1. Legal/compliance review

**Demo substitute:** `docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` — a list of what needs
review, not the review itself.

**Non-binding estimate:** Initial healthcare-privacy/BAA analysis plus a first pass at
worker-classification and cargo/pharmacy-regulatory review from a qualified firm commonly runs from
the low five figures for a narrowly-scoped opinion up to well into six figures for a fuller
multi-area engagement (privacy + employment + regulatory + contract drafting) — highly dependent on
firm, hourly rates, and how many of the ten checklist items are engaged at once. Verify with
counsel directly; do not budget from this range alone.

## 2. Business/commercial insurance

**Demo substitute:** `Vehicle.plate_number`/`InsuranceStatus` are placeholder fields; no real policy
exists or is referenced anywhere.

**Non-binding estimate:** Small-business general liability insurance commonly runs in the
low-to-mid four-figures annually for a small operation; commercial auto/cargo (bailee) coverage for
a courier fleet is typically priced per-vehicle and by cargo value/type, plausibly adding another
low-to-mid four figures per vehicle annually, with healthcare-adjacent cargo (even "routine
specimens") likely commanding a premium over a generic parcel-courier policy. Professional/liability
coverage specific to handling medical-adjacent cargo may be a separate line item entirely. **Verify
all of this with a licensed commercial insurance broker** — this is exactly the kind of quote that
depends heavily on claims history, fleet size, and coverage limits chosen, none of which this
document can estimate meaningfully.

## 3. Courier background and motor-vehicle checks

**Demo substitute:** No real provider; `docs/TECH_STACK_AND_ZERO_COST_POLICY.md` explicitly
prohibits Checkr and all paid equivalents as a *required* dependency of this repository — this is a
deliberate zero-cost-prototype constraint, not a claim that background checks are unnecessary.

**Non-binding estimate:** Commercial background-check providers commonly price per-check in the
low tens of dollars for a standard package (identity, criminal history, motor-vehicle record), with
volume discounts at scale; a driving-record (MVR) pull alone is typically priced lower, often in the
single-to-low-double-digit dollars per pull, sometimes offered as a recurring monitoring
subscription per courier rather than a one-time check. **Verify current pricing directly with a
provider** (e.g. Checkr or a comparable FCRA-compliant vendor) — pricing tiers and compliance-package
inclusions change.

## 4. Courier equipment/PPE

**Demo substitute:** `Equipment`/`Vehicle.supports_refrigeration` are data-model flags describing
equipment a courier is assumed to already have; nothing in this repository purchases, tracks
inventory of, or reimburses for physical equipment.

**Non-binding estimate:** Insulated/refrigerated transport bags or coolers with temperature
indicators are commonly priced in the tens to low hundreds of dollars per unit depending on
capability; basic PPE (gloves, sanitizer, spill kit) is a comparatively minor per-courier recurring
cost. Scale this by fleet size and by how many couriers need refrigerated-capable equipment
specifically (this prototype's seeded demo data shows a realistic split — some couriers
ambient-only, some refrigerated-capable).

## 5. Operational dispatch staff

**Demo substitute:** The dispatch console (Phase 4) is a real tool for a human dispatcher to use —
it recommends, scores, and lets a dispatcher assign/reassign/override with a reason. It does not
replace the human role; a real pilot needs actual dispatcher labor hours, not just the software.

**Non-binding estimate:** This is a genuine staffing/payroll cost, not a software-infrastructure
line item, and depends entirely on operating hours (per `docs/PRODUCT_REQUIREMENTS.md` section 2:
weekdays 7 AM-8 PM plus limited evening/weekend STAT coverage) and headcount needed for that
coverage window — this document deliberately does not estimate labor cost, since it varies by
locality, employment structure (see the worker-classification legal-review item), and shift design
far more than any technology choice does.

## 6. Reliable SMS/communications at scale

**Demo substitute:** `SimulatedSmsProvider` (`apps/notifications/providers.py`) makes zero real
network calls of any kind, ever — it only writes a local `SmsLogEntry` row. Email goes to a local
Mailpit capture inbox, never a real inbox.

**Non-binding estimate:** Commercial transactional-SMS providers (e.g. Twilio or a comparable
equivalent — named here only as a market reference point, not a recommendation, and explicitly
prohibited as a *required* dependency of this repository per the zero-cost policy) commonly price
per-message in the low cents range in the US, plus a small monthly phone-number rental fee; email
transactional-delivery services are typically priced per-thousand-messages at a comparably low rate
or offer a free tier for low volumes. At this prototype's projected pilot scale (a controlled
Manhattan-Brooklyn zone, not a national rollout), monthly SMS/email cost is plausibly a low-hundreds
figure, but this depends entirely on message volume per delivery (status updates, recipient links,
credential-expiration alerts) once real triggers are wired in (see the notification-wiring gap in
`docs/PILOT_READINESS/GAP_ASSESSMENT.md` section 7) — **verify current per-message pricing directly
with a provider.**

## 7. Mapping/routing infrastructure at scale

**Demo substitute:** Every distance/ETA calculation in this codebase is a synthetic haversine
straight-line estimate (`apps.deliveries.pricing.estimate_distance_km`) — no real routing engine
(OSRM or otherwise) was ever wired in at any phase, despite being named as the intended demo
approach in `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`.

**Non-binding estimate:** Self-hosting OSRM (open-source, no license fee) against OpenStreetMap
data for a single-borough-pair service zone is computationally modest and could plausibly run on a
low-cost virtual machine (a small-to-mid-tier monthly VM cost, likely double-digit dollars/month),
making this one of the cheaper line items if self-hosted, at the cost of needing to
build/maintain/re-download OSM extracts. A paid routing/geocoding API (explicitly prohibited as a
*required* dependency by this project's zero-cost policy, but relevant context for a real pilot that
lifts that constraint) would instead be priced per-request, commonly in a free-tier-then-per-1000-
requests model at low pilot volumes. **Verify current OSRM hosting-resource sizing and/or paid-API
pricing directly** — this is one of the more genuinely variable estimates in this document, since it
depends heavily on request volume and self-host-vs-managed choice.

## 8. Production hosting/backups

**Demo substitute:** No production hosting exists (`docs/HOSTING_OPTIONS.md` is a recommendation
only). The stack (`web`, `worker`, PostgreSQL/PostGIS, Valkey, Mailpit) runs entirely via local
`docker compose` on a developer machine today.

**Non-binding estimate:** `docs/HOSTING_OPTIONS.md`'s own survey found that every genuinely free
PaaS tier fails at least one hard requirement (a persistent background worker process, a persistent
non-expiring Postgres database, or a no-credit-card constraint) for this stack — so a real pilot
almost certainly needs a paid tier. A managed Postgres instance plus a small always-on
web/worker compute tier for this workload's likely pilot scale (a single-service-zone courier
network, not high-traffic consumer-scale) commonly starts in the range of double-to-low-triple-
digit dollars per month for entry-level managed offerings, scaling up with data volume, background
job throughput, and redundancy/backup requirements. Managed Redis/Valkey is typically a separate
line item in the same range. Off-host, scheduled, encrypted backups (none of which exist in this
prototype beyond one manual, executed local drill) add further modest recurring cost.
**Verify current pricing directly with candidate providers at decision time** — free-tier and
entry-tier pricing in this market changes frequently, exactly as `docs/HOSTING_OPTIONS.md` itself
warns.

## 9. Payment processing

**Demo substitute:** `PaymentStatus` is a plain, manually-set mock field; no real payment processor
of any kind is integrated, and `apps/billing/models.py`'s own docstring states one never should be
built directly into this repository (per CLAUDE.md's do-not-build list).

**Non-binding estimate:** Standard commercial card/ACH payment-processing fees (for a real processor
this repository would integrate behind a `PaymentProvider` adapter, never storing raw card data
directly — see `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md`) commonly run in the low
single-digit percent of transaction value for card payments, often lower for ACH/bank transfers, plus
typically a small per-transaction flat fee for card payments. B2B invoicing at this pilot's likely
customer count (a handful of healthcare organizations, not thousands of consumer transactions) may
also reasonably use a simpler invoicing/ACH flow rather than card processing at all, which would
shift this cost profile. **Verify current processor pricing directly** — and note item 2's insurance
review and item 1's legal review both bear on which payment model (invoicing vs. card) is even
appropriate here.

## 10. Potential security/compliance assessments

**Demo substitute:** `docs/THREAT_MODEL.md` is a real, specific, self-authored threat model — not an
independent third-party assessment.

**Non-binding estimate:** An independent third-party security assessment (penetration test or
focused application security review) of an application at roughly this scope/complexity commonly
runs from the low five figures for a focused, scoped engagement upward, depending on depth
(automated scan vs. manual pentest vs. full compliance-oriented assessment) and whether a specific
compliance framework's assessment requirements apply (which loops back to the HIPAA/BAA legal
determination in the legal-compliance checklist). **Verify with an actual security assessment firm**
— scope and depth matter more to this number than almost any other line item here.

---

## Summary table

| # | Category | Demo substitute | Estimate is | Verify with |
|---|---|---|---|---|
| 1 | Legal/compliance review | Checklist only, no review performed | Low five to six figures (one-time) | Qualified counsel |
| 2 | Business/commercial insurance | Placeholder fields, no real policy | Low-to-mid four figures/year (rough) | Licensed insurance broker |
| 3 | Background/MVR checks | No real provider (policy-prohibited) | Low tens of $/check | Background-check vendor |
| 4 | Courier equipment/PPE | Data-model flags only | Tens-to-low-hundreds $/unit | Equipment vendor |
| 5 | Operational dispatch staff | Software tool exists; no staff | Not estimated (labor/locality-dependent) | Ops/HR planning |
| 6 | SMS/communications at scale | Fully simulated, zero real calls | Low cents/message; low-hundreds $/month at pilot scale | SMS/email provider |
| 7 | Mapping/routing at scale | Synthetic haversine only | Double-digit $/month (self-host) or per-request (API) | OSRM hosting or routing API vendor |
| 8 | Production hosting/backups | None exists | Double-to-low-triple-digit $/month | Managed hosting/DB provider |
| 9 | Payment processing | Mock field only | Low single-digit % + flat fee/transaction | Payment processor |
| 10 | Security/compliance assessment | Self-authored threat model only | Low five figures+ (one-time) | Security assessment firm |

Every number above is illustrative, not actionable — see the per-item detail for the reasoning and
caveats. See `docs/PILOT_READINESS/GO_NO_GO_REPORT.md` for how these costs factor into an overall
pilot-readiness recommendation.
