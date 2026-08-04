# Go/No-Go Report — Pilot Readiness Synthesis

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

## What this report is, and is not

This report synthesizes `docs/PILOT_READINESS/GAP_ASSESSMENT.md`,
`docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md`,
`docs/PILOT_READINESS/BUDGET_CHECKLIST.md`, and
`docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md` into one structured picture: what is
genuinely solid, what is a hard blocker, and what is an addressable engineering gap. **This report
does not authorize a real pilot.** It is an evidence-based input for the project owner's own
decision, not a substitute for it, and not a substitute for the professional reviews named in the
legal/compliance checklist. Per `docs/IMPLEMENTATION_ROADMAP.md` Phase 10's own text, reaching the
end of this phase does not authorize connecting real PHI, real deliveries, real payments, real
background checks, or real production communications — that requires the professional reviews below
plus the project owner's own, separate, explicit decision.

---

## 1. What's genuinely solid — verified evidence, not assertion

These are claims this session either independently re-verified against the actual code (not just
re-read from `docs/CURRENT_STATUS.md`'s own account), or that were themselves the product of
multiple independent verification passes across this project's development:

- **The custody hash-chain tamper-detection mechanism is real and was independently, repeatedly
  verified.** `apps.custody.hashing.compute_event_hash` is the single function both the writer
  (`apps.custody.services.record_event`) and the verifier
  (`apps.custody.verification.verify_custody_chain`) call, so they cannot silently drift apart.
  `apps/custody/tests/test_verification.py` proves detection of a genuine raw-SQL tampering attempt
  against a historical event (bypassing the ORM guard entirely, not just testing the ORM guard
  itself) — confirmed present in the codebase and unchanged through Phase 9 by this session's own
  inspection.
- **Tenant isolation is real and tested at the HTTP layer, not just the queryset layer.**
  `apps.organizations.services.scope_queryset_to_user_orgs` plus explicit per-view permission checks
  are exercised by cross-tenant isolation tests at both layers
  (`apps/organizations/tests/test_services.py`, `apps/deliveries/tests/test_views.py::
  test_cannot_view_other_org_delivery_request_via_http`, `apps/audit/tests/test_views.py`) — this
  session confirmed the permission-helper pattern is used consistently across every sensitive view
  cited in `docs/THREAT_MODEL.md` section 1.
- **The atomic-assignment concurrency guarantee was verified against a real PostgreSQL container,
  not just SQLite.** Phase 4's `assign_delivery` concurrency test was run repeatedly (15+ times) both
  against SQLite and against a real, throwaway `postgis/postgis:17-3.5` container — 100% correctness
  in both cases (exactly one winner, one clean `AssignmentConflictError`, never a double-assignment,
  never a crash). A later, separate SQLite-only test-flakiness artifact (shared-cache-mode deadlock
  detection, investigated and mitigated in Phase 8) was correctly traced to the *test harness*, not
  the underlying database-level `UniqueConstraint` that provides the actual correctness guarantee.
- **The hard-eligibility gate genuinely cannot be bypassed by a dispatcher override.** Dedicated
  tests (`test_hard_eligibility_gate_cannot_be_overridden_via_{assign,offer,reassign}_delivery`)
  confirm every dispatch entry point rejects an ineligible courier even with a plausible-sounding
  override reason supplied, and writes nothing to the database in that case — this is enforced
  structurally (the eligibility check runs unconditionally before any write), not by convention.
- **The zero-cost policy is genuinely enforced, not aspirational.** `python manage.py audit_cost`
  fail-closed-checks every dependency against an explicit allowlist and scans source for
  prohibited-service indicator strings; this session confirmed `.github/workflows/ci.yml` runs it in
  CI on every push/PR, and `docs/COST_AUDIT.md` (generated, not hand-edited) currently reports 24
  dependencies, 0 prohibited-service indicators.
- **A real backup/restore drill was actually executed, not just documented.** `docs/BACKUP_RESTORE.md`
  records a genuine `pg_dump`/`DROP DATABASE`/`pg_restore` round-trip against a real PostGIS
  container with before/after row-count and data verification — a paper procedure was not
  substituted for an executed one.
- **The accessibility pass found and fixed real violations, not a vacuous clean scan.**
  Phase 8's axe-core pass found genuine `serious`-impact color-contrast violations across all six
  scanned pages on the first run, fixed them, and re-scanned clean — the "before" state is evidence
  the tooling actually works, not that nothing was ever wrong.
- **This project's own self-honesty is itself a real, checkable asset.** Across all ten phases,
  `docs/CURRENT_STATUS.md` consistently reports coverage gaps, flaky tests, and deliberately
  unimplemented features by name rather than omitting them — this session's own spot-checks (grep
  for `CourierPerformanceSnapshot`, the custody append-only mechanism, MFA enrollment logic,
  PostGIS usage, CI's dependency-scanning coverage) confirmed every claim checked was accurate as
  stated, with zero instances found of a claimed capability that didn't actually exist in the code.

## 2. Hard blockers for ANY real pilot — not engineering problems

These cannot be resolved by more development work. Every item in
`docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` is in this category:

- **Healthcare privacy/business-associate (HIPAA/BAA) status** — undetermined, and gates nearly
  every other legal item (see section 4 below for why this is the recommended first step).
- **Customer/business-associate contracts** — none exist; no software feature can substitute for a
  negotiated legal agreement.
- **Specimen/infectious-substance packaging eligibility and pharmacy medication-delivery rules** —
  this software's enforcement is structural-plus-keyword-blocklist only, explicitly not a compliance
  control; real regulatory review is required before any Class 2/3 cargo is ever handled for real.
- **New York worker classification** — this software's data model is deliberately classification-
  agnostic; the actual employment-law determination is independent of anything built here.
- **Insurance and vehicle requirements, background-check consent/process, incident/exposure plan,
  data retention policy, production hosting/security review** — each requires an independent
  professional (broker, FCRA/employment counsel, ops/legal policy owner, security assessor) whose
  determination this software cannot make on its own behalf.

## 3. Addressable engineering gaps — real, but not fundamental redesigns

These are gaps a real pilot needs closed, but each is closable with focused engineering effort on
top of the existing architecture, not a rewrite:

- **Real routing/distance** (haversine → OSRM or a routing API) — one new adapter plus replacing two
  call sites (`estimate_distance_km`, `compute_sla_estimate`).
- **Real SMS notifications** — the `NotificationProvider` interface already exists and was
  spot-checked this session to substantially support a real implementation with no caller changes,
  modulo adding a small factory/settings seam.
- **Database-level append-only enforcement** for custody/audit/status-transition tables — a
  restricted Postgres role or trigger layered on top of the existing ORM guards and hash chain, not
  a redesign of the hash-chain mechanism itself.
- **Dependency-vulnerability scanning in CI** (`pip-audit`/Dependabot) — a CI-workflow addition, no
  application-code change.
- **Mandatory MFA for privileged roles**, **courier location retention/purge policy**, **a harder
  per-token PIN lockout**, **object storage for signature images**, **real background-check
  provider integration (with the net-new file-upload capability it requires)** — each is a bounded,
  well-scoped addition to an existing, working subsystem, not a new architecture.
- **A production hosting deployment** — no technical blocker exists; `docs/HOSTING_OPTIONS.md`
  already surveys the realistic options and their trade-offs; this is a provisioning/budget decision
  once the legal blockers above are cleared, not an engineering unknown.

The distinction that matters: nothing in this list requires this application's fundamental
architecture (modular Django monolith, adapter-interface pattern, tenant-scoped multi-tenancy,
append-only-plus-hash-chain custody model) to change. Every gap above is additive.

## 4. Final statement

**(a) This report does not itself authorize a pilot.** It is a synthesis of evidence for the project
owner's own decision-making, not a decision.

**(b) A real pilot requires every professional review listed in
`docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md`, completion of the addressable engineering gaps
in section 3 above that the reviews determine are actually required, and the project owner's own
explicit, separate decision to proceed** — not implied by, and not a natural consequence of, this
document existing or this phase being "complete."

**(c) Recommended concrete next step, if the owner wants to move toward a real pilot:** **engage
healthcare privacy counsel first**, specifically to determine business-associate status and BAA
requirements (`docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` item 1). This is recommended as
the literal first step — not "do more review" in the abstract — because it is the one item most
other legal and technical decisions depend on: it determines whether a BAA is needed with each
customer organization (which shapes the contracts in item 2), what technical/administrative
safeguards this application would be contractually obligated to add beyond what exists today (which
reprioritizes the engineering gaps in section 3), and what data-retention policy is even legally
permissible (item 9). Every other checklist item can, in principle, proceed in parallel, but this
one is the load-bearing determination the rest of the legal picture hangs off of, and is the single
highest-leverage next action available to the project owner today.
