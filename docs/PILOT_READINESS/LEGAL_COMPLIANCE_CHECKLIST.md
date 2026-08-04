# Legal and Compliance Review Checklist

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

**This document is not legal advice.** It does not evaluate legal risk, does not conclude that any
item below is satisfied or unsatisfied in a legal sense, and does not substitute for review by a
licensed attorney or qualified compliance professional in the relevant jurisdiction (New York State
and applicable federal law). For each item required by
`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8, this document states plainly: what this
codebase currently does (if anything relevant), and why that is not, and cannot be, a substitute for
qualified professional review. Every item below is a **hard blocker** for any real pilot — none of
them can be resolved by writing more code.

---

## 1. Healthcare privacy and business-associate status (HIPAA BAA analysis)

**What the software does:** Nothing in this repository processes, stores, or transmits protected
health information (PHI). `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 2 is enforced throughout
— no field for diagnoses, lab results, clinical notes, medication indications, SSNs, insurance
identifiers, or full patient records exists anywhere in `apps/*/models.py` (re-verified by this
session's spot-check of the Phase 8 PHI sweep's own methodology and findings, `docs/CURRENT_STATUS.md`
"Phase 8" PHI sweep section). Operational references (delivery ID, package barcode, synthetic
accession codes, facility/organization IDs, operational contact names/roles like "Front Desk") are
used instead throughout.

**Why this requires qualified review anyway:** A real pilot moving specimens/documents/medications
between healthcare organizations will very likely make MedRelay a HIPAA "business associate" of at
least some customer organizations, regardless of how carefully the *software* avoids storing PHI
directly — HIPAA's business-associate definition turns on the *relationship and function*
(handling PHI-adjacent items on behalf of a covered entity), not merely on whether a database column
is named `diagnosis`. Whether operational metadata like "which specimen went from which clinic to
which lab, when" itself constitutes PHI in context, whether a Business Associate Agreement (BAA) is
required with each customer organization, and what technical/administrative safeguards a BAA would
obligate MedRelay to implement (which likely exceed what this demo-scale prototype currently has —
see `docs/PILOT_READINESS/GAP_ASSESSMENT.md` sections 6 and 8) is a determination only healthcare
privacy counsel can make. **This is the single gating item most other items depend on** — see
`docs/PILOT_READINESS/GO_NO_GO_REPORT.md`'s recommended next step.

## 2. Customer/business-associate contracts

**What the software does:** Nothing. There is no contract-management functionality anywhere in this
codebase — `Organization`/`OrganizationMembership` model a customer's *technical* access, not a
commercial or legal relationship. No customer organization in this system has ever been bound by
any real contract; `seed_demo_data`'s three organizations are entirely synthetic and their names are
explicitly marked `(Demo)`.

**Why this requires qualified review:** Every real customer relationship needs its own commercial
services agreement and, per item 1, likely a BAA. Terms around liability, SLA remedies, data
ownership/retention, incident-notification obligations, and termination need drafting and
negotiation — none of which is a software problem.

## 3. Specimen/infectious-substance eligibility and packaging rules

**What the software does:** `docs/PRODUCT_REQUIREMENTS.md` section 3 defines exactly three cargo
classes (documents/non-hazardous supplies, approved routine specimens, sealed non-controlled
medication) and explicitly excludes Category A infectious substances, controlled substances, human
organs, radioactive material, regulated medical waste, loose sharps, unsealed specimens, specialized
blood products. This exclusion is partially structural (`CargoClass` has exactly 3 seeded rows, no
UI/API path creates a 4th) and partially a crude, explicitly-non-compliance-grade keyword scan over
free-text fields (`apps.cargo.validation.find_prohibited_cargo_keywords` — trivially evaded by
misspelling or synonym, stated plainly in its own docstring). The system also requires a
"packaging/classification attestation" before a delivery can be dispatched, but this attestation is
a **customer self-attestation**, never independently verified by the system.

**Why this requires qualified review:** DOT/IATA/other regulatory packaging and labeling rules for
even "approved routine specimens" (UN 3373, Category B biological substances, etc.) are a real,
detailed regulatory area this software makes no attempt to enforce beyond a keyword blocklist.
Whether the "Class 2" routine-specimen category as scoped is even legally deliverable by a
non-specialized courier network, what training/certification the couriers themselves would need,
and what liability exists if a customer's self-attestation is wrong all require review by counsel
and/or a DOT-hazmat-transport specialist — not something a demo prototype's validation logic can
resolve.

## 4. Pharmacy medication delivery rules

**What the software does:** "Class 3" cargo (sealed, non-controlled, pharmacy-prepared medication)
exists as a selectable cargo class. The product requirements explicitly state "the pharmacy/facility
remains responsible for lawful dispensing, packaging, labeling, and release" — the software performs
no pharmacy-licensing check, no controlled-substance screening beyond the same keyword blocklist as
item 3, and no chain-of-custody requirement specific to pharmacy law.

**Why this requires qualified review:** New York (and federal, for anything touching controlled
substances even tangentially) pharmacy-delivery regulation is a specialized legal area. Courier
licensing/registration requirements for pharmacy deliveries, patient-identity verification at
hand-off (this prototype's recipient PIN is an operational proxy, not a legally-specified identity
check), and liability allocation between pharmacy and courier all need pharmacy-law counsel review
before this cargo class is ever used for a real delivery.

## 5. New York worker classification (employee vs. independent contractor)

**What the software does:** Nothing. `CourierProfile` models an onboarding/eligibility/availability
relationship (approved/suspended/inactive status, credentials, shift availability, job
accept/reject) that is agnostic to worker-classification law — the system does not encode or imply
either an employment or an independent-contractor relationship; it simply tracks operational
state. Couriers can accept or reject job offers (`JobOffer.decline_reason` exists and is recorded),
which is a fact pattern relevant to (but not dispositive of) worker-classification analysis.

**Why this requires qualified review:** New York State (and NYC-specific) worker-classification law
for gig/platform-mediated courier work is an active, consequential, and litigated legal area
(misclassification carries real wage-and-hour, unemployment-insurance, and workers'-compensation
liability). Whether MedRelay's actual operational control over couriers (degree of control over
schedule, equipment, exclusivity, training requirements) makes couriers employees or contractors as
a matter of law is a fact-specific determination for employment counsel, not something this
software's data model can settle by construction.

## 6. Insurance and vehicle requirements

**What the software does:** `Vehicle`/`Equipment`/`CourierProfile` model an `InsuranceStatus`
enum and a `plate_number` field, both explicitly documented as synthetic placeholders
(`Vehicle.plate_number` is stated in `apps/couriers/models.py`'s own docstring/help text to be a
synthetic placeholder, never a real plate). No verification of any insurance policy, vehicle
registration, or motor-vehicle record happens anywhere — the fields are manually set by an internal
reviewer, with no integration to any real DMV or insurance-verification service.

**Why this requires qualified review:** Real commercial-auto insurance requirements for a courier
network transporting healthcare-adjacent cargo (minimum coverage limits, whether personal auto
policies suffice or commercial coverage is required, cargo/bailee insurance for lost/damaged
specimens or medication) need a licensed insurance broker's assessment specific to this business
model — not a software gap, a business-formation one. See
`docs/PILOT_READINESS/BUDGET_CHECKLIST.md` for rough, non-binding cost-range context once that
review happens.

## 7. Background-check consent/process

**What the software does:** Nothing real. `IdentityReviewStatus` is a manually-set placeholder
enum; `CourierCredential`'s admin UI literally labels one credential type "Background Check
(Placeholder — No Real Provider Integrated)" (verbatim string, confirmed in
`apps/couriers/models.py` this session). No consent flow, no real background-check vendor
integration (Checkr and all paid equivalents are explicitly prohibited by
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`), and no FCRA-style adverse-action process exists anywhere
in this codebase.

**Why this requires qualified review:** Background checks on individuals who will access healthcare
facilities and handle healthcare-adjacent cargo are subject to the Fair Credit Reporting Act (FCRA)
at the federal level and New York State background-check law, both of which impose specific
consent-disclosure, adverse-action-notice, and dispute-process requirements that have nothing to do
with which vendor API you call — they need a compliance process designed with counsel regardless of
which real `BackgroundCheckProvider` is eventually integrated (see
`docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md`).

## 8. Incident/exposure plan

**What the software does:** A real, working incident *console* exists (Phase 6): twelve incident
categories including "courier injury/exposure," severity levels, a hold-until-resolved state-machine
guarantee, and an append-only (ORM-level) action history. This is genuine operational tooling for
*recording* an incident once one has happened.

**Why this requires qualified review:** The software has no opinion on, and does not encode, what
the *actual* organizational response plan is when a courier reports an exposure to a biohazard, a
vehicle accident, or a suspected-tampering event — who is notified, within what timeframe, what
OSHA-adjacent reporting obligations trigger (this system's `CLAUDE.md` explicitly disclaims any OSHA
compliance claim), and what medical/legal follow-up is owed to the courier. That is an operational
and legal policy a real pilot must design and document independently of this software; the incident
console can *record* whatever process is decided, but does not define one.

## 9. Data retention policy

**What the software does:** No explicit, configured data-retention/purge policy exists for any
model in this codebase. `CourierLocationPing` rows accumulate indefinitely (explicitly flagged in
`docs/THREAT_MODEL.md` section 5); custody events, audit events, and delivery records are, by
design, meant to be kept (append-only, tamper-evident) but no maximum retention window or scheduled
purge job exists for any of them.

**Why this requires qualified review:** Even setting aside item 1's BAA question, real courier
location history, incident records, and delivery metadata are the kind of data a real business needs
a deliberate retention/deletion policy for — driven by whatever contractual/regulatory retention
obligations apply once items 1-2 above are resolved, and by ordinary data-minimization/privacy-law
principles (which apply regardless of HIPAA status). This is a policy decision for legal/compliance
to set; the software has the technical primitives to implement a retention job once a policy exists,
but no policy exists today.

## 10. Production hosting/security

**What the software does:** `docs/HOSTING_OPTIONS.md` (Phase 9) is a hosting *recommendation*
document only — no hosting account was ever created, no platform selected, nothing deployed
anywhere. `config/settings/prod.py`/`demo.py` configure reasonable web-application defaults (HSTS,
secure cookies, SSL redirect) for *whenever* a real deployment happens, but no such deployment has
ever existed. A real, executed backup/restore drill was performed once, manually, in a development
environment (`docs/BACKUP_RESTORE.md`) — not as a standing, scheduled, off-host backup process.
`docs/THREAT_MODEL.md` documents this codebase's specific threat surface and residual risks
in detail, including several noted above (ORM-level-not-DB-level append-only guards, no
dependency-vulnerability scanning in CI, MFA opt-in).

**Why this requires qualified review:** A real pilot handling any data with actual privacy/business
stakes needs an independent security assessment (not self-assessment) of the production environment
once one is chosen — this is standard practice for any real B2B SaaS handling business-sensitive
data, entirely independent of whether HIPAA ultimately applies. `docs/THREAT_MODEL.md` is a useful
starting input for that review, not a substitute for it.

---

## Summary table

| # | Item | Software does today | Requires professional review before pilot |
|---|---|---|---|
| 1 | HIPAA/BAA status | No PHI fields exist; BA-relationship status undetermined | **Yes — healthcare privacy counsel** |
| 2 | Customer/BA contracts | No contract-management functionality | **Yes — counsel to draft/negotiate** |
| 3 | Specimen/infectious-substance rules | Structural + keyword-blocklist exclusion only | **Yes — DOT-hazmat/counsel** |
| 4 | Pharmacy delivery rules | Cargo class exists; no pharmacy-law enforcement | **Yes — pharmacy-law counsel** |
| 5 | NY worker classification | Operational model is classification-agnostic | **Yes — employment counsel** |
| 6 | Insurance/vehicle requirements | Placeholder fields only, no verification | **Yes — licensed insurance broker** |
| 7 | Background-check consent/process | Placeholder fields only, no real provider | **Yes — FCRA/NY-law counsel** |
| 8 | Incident/exposure plan | Incident console exists; no response-plan policy | **Yes — ops/legal policy design** |
| 9 | Data retention policy | No retention/purge policy configured anywhere | **Yes — legal/compliance policy design** |
| 10 | Production hosting/security | No production deployment exists; threat model documented | **Yes — independent security assessment** |

Every row above is a hard blocker, not a nice-to-have — none is resolved by additional software
engineering alone. See `docs/PILOT_READINESS/GO_NO_GO_REPORT.md` for how this rolls into an overall
recommendation and a concrete suggested first step.
