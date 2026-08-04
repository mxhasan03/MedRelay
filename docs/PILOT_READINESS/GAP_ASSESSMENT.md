# Gap Assessment — Demo Prototype vs. Real Operating Pilot

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

**Purpose and scope boundary.** This document is Phase 10 (`docs/IMPLEMENTATION_ROADMAP.md`) work:
a documentation/assessment deliverable, not a code change. It lists every meaningful gap between
"what this demo prototype does" (verified against `docs/CURRENT_STATUS.md`'s ten phase sections and
spot-checked directly against the code in this session) and "what a real operating courier pilot
would need." It does not authorize a pilot, does not recommend a launch date, and does not itself
constitute legal, compliance, security, or business advice — see
`docs/PILOT_READINESS/GO_NO_GO_REPORT.md` for how these gaps roll up into an overall readiness
picture, and `docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` for the professional-review gates
that are hard blockers regardless of any of the technical gaps below.

Organized by the same domain areas as `docs/IMPLEMENTATION_ROADMAP.md`'s phases. Each gap cites the
specific phase/file it originates from, per this session's own re-verification against the current
code (not just against what `CURRENT_STATUS.md` claimed when it was written).

---

## 1. Identity, tenancy, and roles (Phase 1)

- **Tenant isolation is enforced per-view, not by a database-level policy.** Every sensitive view
  calls an explicit permission helper (`apps.organizations.services.can_view_organization` etc.),
  but there is no Postgres Row-Level Security policy or single global middleware backstopping this —
  a new view that forgets the check is a real, structural risk. Cited: `docs/THREAT_MODEL.md`
  section 1.
- **No self-service signup for organizations, facilities, or couriers.** Account provisioning is
  admin/`seed_demo_data`-only by design (Phase 1) — a real pilot's onboarding flow (sales,
  compliance review, contract signing) does not exist in software at all.
- **Password-reset views are routed but have no templates** (dead surface since Phase 1) — would
  500 if visited directly; nothing links to them, but this should be templated or removed before
  any real user-facing deployment.
- **`FacilityReceivingRule` seed data is a uniform weekday/weekend default**, not tailored per
  facility type (Phase 1) — a real facility (e.g. a 24-hour hospital dock) would need its own hours.

## 2. Cargo policy and delivery requests (Phase 2)

- **Prohibited-cargo guard is a crude, case-insensitive keyword scan**
  (`apps.cargo.validation.find_prohibited_cargo_keywords`), explicitly not a compliance control —
  trivially evaded by misspellings, synonyms, or non-English text. The structural defense (only 3
  fixed `CargoClass` rows exist, no UI/API path creates a 4th) is real but only prevents formally
  *selecting* an excluded category, not describing one in free text.
  Cited: Phase 2 design decision 6, `apps/cargo/validation.py`.
- **No real routing/distance API anywhere in the codebase.** Distance is a synthetic haversine
  straight-line estimate between stored facility coordinates
  (`apps.deliveries.pricing.estimate_distance_km`, confirmed present in the code at
  `apps/deliveries/pricing.py:66-100` this session) — reused as-is by Phase 4's SLA/ETA
  calculations (`apps.dispatch.sla`). Self-hosted OSRM, named in
  `docs/TECH_STACK_AND_ZERO_COST_POLICY.md` as the intended demo routing engine, was never wired
  in at any phase. A real pilot needs real turn-by-turn/road-network distance and traffic-aware ETA.
- **`RecurringRoute` has no generation job** — `generate_delivery_requests_for_recurring_route`
  raises `NotImplementedError` unconditionally (Phase 2, still true; confirmed unchanged through
  Phase 9). A real pilot's recurring-route customers get no automatic scheduled generation of
  delivery requests at all.
- **`Quote` keeps one current row per delivery request**, not a quote history table; pricing is
  fully synthetic/admin-editable (`PricingRule`), not a real commercial rate card.
- **`DeliveryStatusTransition` append-only enforcement is ORM-level only** (see section 6 below —
  this pattern recurs across every append-only model in this codebase).

## 3. Courier onboarding and eligibility (Phase 3)

- **No real background-check provider integration of any kind.** `IdentityReviewStatus`/
  `DriverLicenseStatus`/`InsuranceStatus` are placeholder, manually-set enums; `CourierCredential`
  admin literally labels one credential type "Background Check (Placeholder — No Real Provider
  Integrated)" (confirmed at `apps/couriers/models.py:195` this session). No `BackgroundCheckProvider`
  adapter exists as code anywhere (see `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md`).
- **No file upload for credential evidence at all** — `CourierCredential.evidence_reference` is a
  placeholder text field (e.g. a synthetic filename string), never a real uploaded document; there
  is no `FileField`/`ImageField`/upload view anywhere in `apps.couriers`, confirmed unchanged through
  Phase 9.
- **`CourierPerformanceSnapshot` was never built**, in any phase — confirmed by grep across the
  full repository in this session (only docstring/architecture-doc mentions exist, no model). The
  dispatch score's "reliability/on-time history" factor is a hardcoded neutral 0.5 constant because
  no completed-delivery outcome data has ever existed in this codebase to compute a real value from
  (Phase 4 design decision 3). A real pilot needs a genuine on-time/reliability history to make
  dispatch scoring meaningfully differentiate couriers.
- **`eligible_couriers_for`/`eligible_deliveries_for` are O(n×m) Python-level filters**, not
  optimized database queries — acceptable at demo data volumes (tens of couriers/deliveries), a real
  scaling concern at pilot volumes.
- **Courier-status history is a plain field, not an audit log** (deferred to Phase 8's audit
  viewer, which only covers auth/membership events, not courier status — see section 6).
- **`TrainingRecord` is not wired into the eligibility engine at all** — no cargo class currently
  requires a specific training certification in this prototype's rules.

## 4. Dispatch and operations console (Phase 4)

- **The explainable score's "reliability" and "customer preference" factors are honest
  placeholders**, not real signals (see section 3 above and Phase 4 design decision 3) — the score
  is real math over partially-synthetic inputs, not a demonstration of a fully-realized scoring
  model.
- **`eta_to_pickup` has no real distance signal** — it is a small set of synthetic zone-match minute
  tiers (15/25/40 min), since no courier-location model existed until Phase 5, and even Phase 5's
  location pings are never fed back into dispatch scoring.
- **Concurrency-correctness confidence is real but bounded.** The atomic-assignment race test was
  run repeatedly (15-60+ runs) against both SQLite and a real, throwaway single-container
  PostgreSQL instance with 100% correctness (no double-assignment observed ever) — but this proves
  correctness under one pair of concurrent requests against an idle container, not under sustained
  production-level concurrent load, connection-pool exhaustion, or multi-row deadlock scenarios
  (explicitly stated as out of scope in Phase 4's own write-up).
- **A SQLite-specific test-flakiness artifact was found, explained, and mitigated (retried), not
  eliminated** — Phase 8 traced an observed ~10% baseline flake in the concurrency test to SQLite's
  shared-cache-mode deadlock detection (not a correctness bug — every failure was "both threads
  cleanly conflict," never a double-assignment or crash) and added a single, documented retry,
  reducing it to 0/60 in that session's stress test. This is a SQLite-test-harness artifact with **no
  bearing on real PostgreSQL deployment**, where `select_for_update()` takes a genuine row lock with
  no equivalent behavior — stated for completeness, not because it threatens the underlying
  guarantee.
- **The dispatch dashboard has no live map, courier-location display, or incident/temperature
  alerts** — a plain list/table view only; `docs/PRODUCT_REQUIREMENTS.md` section 7's full "control
  tower" wishlist (live map, temperature alerts, expiring-credential flags in one view) is not built.

## 5. Courier PWA and tracking (Phase 5)

- **Camera-based QR scanning is genuinely untested by the automated suite** — the
  `BarcodeDetector`/`getUserMedia` browser path is manually-reviewable only; only the manual
  code-entry fallback has real test coverage.
- **The offline event queue's actual offline→online recovery was never verified with a real browser
  going through a real network-loss/recovery cycle** — service-worker registration and static-shell
  cache population were verified with real Playwright/Chromium; the `localStorage` queue's own
  retry-on-reconnect behavior was not (Phase 5's own stated scope limit, unchanged since).
  Playwright/Chromium browser automation itself is confirmed working in this development
  environment but is not guaranteed to work in every environment this repository is cloned into
  (`--with-deps` needs interactive `sudo`; plain `chromium` install happened to work here).
- **`CourierLocationPing` is not append-only-hardened** (a deliberate, lower-stakes-data scope
  choice) and, more importantly, **has no data-retention/purge policy** — rows accumulate
  indefinitely. Real courier location data (unlike this prototype's synthetic coordinates) would be
  personal/employment-adjacent data needing an actual retention policy. Cited: `docs/THREAT_MODEL.md`
  section 5.
- **No real precision/battery/background-permission handling for courier location tracking.** The
  browser Geolocation API is used directly, in-foreground, with no discussion anywhere in this
  codebase of background-tracking permissions, battery-optimization exemptions, or GPS-accuracy
  degradation — all real, unaddressed mobile-engineering concerns for continuous field tracking.
- **No real navigation/routing summary is shown to the courier** — the synthetic `RoutePlan`/
  `RouteLeg` estimates are not surfaced in the courier PWA at all.

## 6. Custody, proof, temperature, and incidents (Phase 6)

- **Append-only enforcement for `CustodyEvent`, `DeliveryStatusTransition`, and `AuditEvent` is
  ORM-level, not database-level** — confirmed by this session's own grep across all migrations
  (`apps/*/migrations/`): zero `REVOKE`/`CREATE TRIGGER` statements exist anywhere. Each model's
  `save()`/`delete()` and a custom queryset block ordinary Django ORM mutation, and
  `CustodyEvent`'s SHA-256 hash chain (`apps.custody.hashing.compute_event_hash`) makes any bypass
  *detectable after the fact* — but a raw SQL statement, a compromised database credential, or a
  future code path that writes directly to these tables (bypassing `record_event`) could still
  mutate history today. This is the single most-repeated honest limitation across this codebase
  (`docs/THREAT_MODEL.md` section 3; `apps/custody/models.py`, `apps/deliveries/models.py`,
  `apps/audit/models.py` docstrings all state it independently). A real pilot needs an actual
  Postgres-level guard (a restricted application role without `UPDATE`/`DELETE` grants on these
  tables, or a trigger) before this can be called tamper-*proof* rather than tamper-*evident*.
- **No dedicated multi-threaded concurrency test exists for `apps.custody.services.record_event`**
  (unlike `assign_delivery`'s real multi-threaded test) — the same row-lock + partial
  `UniqueConstraint` pattern protects it, but it was never independently stress-tested the way
  dispatch assignment was.
- **Signature images are stored as inline base64 text**, not through a real object-storage
  adapter — fine at this prototype's data volumes, wrong for real deployment scale/durability.
- **Signature/PIN capture is explicitly a prototype, never a legally binding e-signature** — no
  timestamp-authority integration, no cryptographic signing of the drawn image, no identity-proofing
  behind either mechanism. Stated directly in `apps/custody/models.py`'s own docstring.
- **PIN delivery to the recipient is fully manual/out-of-band** — the plaintext PIN surfaces once
  via a flash message on the customer-facing delivery-detail page; there is no automated
  recipient-facing SMS/email delivery channel for it anywhere in this prototype (Phase 6/7).
- **Temperature readings are entirely simulated** (`apps.temperature.management.commands.
  simulate_temperature_readings` — an honestly-labeled synthetic random-walk generator, confirmed
  present and unchanged this session) — there is no real IoT/sensor-hardware integration and no
  claim of validated cold-chain compliance anywhere, per `docs/PRODUCT_REQUIREMENTS.md` section 12.
- **`DELIVERY_SCAN`/`CUSTODY_TRANSFERRED`/`PACKAGE_PREPARED` custody event types are defined in the
  vocabulary but never automatically emitted** by any service function.
- **No incident category/severity restriction by actor role** — a courier can self-declare any of
  the twelve incident categories at any severity (e.g. `CRITICAL` `suspected_tampering`) with no ops
  review gate.
- **A real, quantified SQLite test-flakiness increase was found and left unfixed at the time** (later
  addressed in Phase 8, see section 4) — Phase 6 itself observed and documented a ~6% flake rate
  increase in the dispatch concurrency test caused by its own change (custody-event emission inside
  `assign_delivery`'s transaction), correctly attributed to test-harness behavior, not a correctness
  regression.

## 7. Notifications, recipient tracking, billing, and reports (Phase 7)

- **No automatic notification triggers wired into delivery-status transitions, job offers, or
  incident open/resolve events** — the full `NotificationProvider` mechanism (payload allow-list,
  email/SMS/webhook adapters, dedup) exists and is demonstrated end-to-end at exactly two call
  sites (`notify_invoice_issued`, `notify_recipient_link_issued`); broader lifecycle-event wiring
  was explicitly out of scope to control blast radius on prior phases' tested code.
- **`WebhookDelivery` never performs a real HTTP request** — a deliberate SSRF-avoidance choice
  (confirmed: no `requests`/`urllib` import anywhere in `apps.notifications`), not a partial
  implementation of a real webhook sender. A real pilot's customer-integration webhooks would need
  a genuine outbound HTTP sender behind an explicit egress-control/allowlist policy — none exists.
- **No paid/production SMS or email provider anywhere** — `SimulatedSmsProvider` makes no network
  call of any kind, ever; email goes to a local Mailpit capture box. See
  `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md` for exactly what a live provider would
  need to satisfy the same interface.
- **No real payment processing of any kind** — `PaymentStatus` is a plain, manually-set mock field;
  `apps/billing/models.py`'s own docstring states there is no `PaymentProvider` adapter and there
  "never should be one in this repository." A real pilot needs a genuine payment processor and a
  PCI-scope decision (see the provider-adapter document).
- **No recipient-facing UI links to the recipient tracking link anywhere** — `issue_recipient_link`
  and the token/view mechanism are fully built and tested, but no "generate/send tracking link"
  button exists on the delivery-detail page; the link itself, like the PIN, is relayed manually.
- **`RECIPIENT_LINK_MAX_AGE_SECONDS` (72 hours) is a single hardcoded constant** — no per-delivery
  or per-service-level override.
- **`_next_invoice_number` is a `count()+1` counter, not a `select_for_update()`-guarded sequence**
  — acceptable at this prototype's zero real-world concurrent-invoice-generation volume, a real gap
  at any meaningful transaction volume.
- **Every report is a full, unfiltered dump of an organization's current data** — no date-range or
  status filtering exists on any of the six report types.

## 8. UX, accessibility, security, and demo hardening (Phase 8)

- **MFA is opt-in, not enforced, for any account, including privileged internal-ops and
  customer-org-owner/administrator accounts.** Confirmed by reading
  `apps.organizations.services.is_mfa_eligible` directly this session: it only decides *enrollment
  eligibility*; nothing in this codebase forces enrollment for any role. Login for an unenrolled
  account requires only a password.
- **No dependency-vulnerability scanning is wired into CI.** Confirmed by reading
  `.github/workflows/ci.yml` in full this session: it runs lint/format/type-check/tests/migration-
  check/cost-audit only — no `pip-audit` step, and no `.github/dependabot.yml` exists in the
  repository at all. `audit_cost` checks *cost-policy* compliance (is this dependency free/allowed),
  not upstream package compromise — a materially different kind of supply-chain risk that is
  entirely unaddressed.
- **Recipient PIN/token rate limiting is per-IP/per-token via a shared cache, not a hard per-token
  lockout.** A distributed attacker spreading attempts across many source IPs is still bounded by
  the 5/minute-per-token rate (impractically slow for a 6-digit PIN — ~139 days at that rate per
  `docs/THREAT_MODEL.md` section 2's own math — but not mathematically zero).
- **Upload/input-size limiting is not exhaustive** — several internal-staff-only free-text fields
  (`Organization.notes`, `Facility.notes`/`access_instructions`, `DispatchOverride.reason`,
  `IncidentAction.note`) remain unbounded `TextField`s, reachable only by authenticated
  internal/admin users and still bounded transitively by the global request-size cap, but not
  individually capped the way public/courier-facing fields were.
- **No automated/scheduled backups, point-in-time recovery, or backup-file encryption** — the
  backup/restore drill (`docs/BACKUP_RESTORE.md`) was genuinely executed once, manually, in this
  development environment; nothing in this repository runs it on a schedule or ships it anywhere
  off-host.
- **Tenant isolation, custody append-only enforcement, and PIN rate-limiting residual risks are all
  independently restated in `docs/THREAT_MODEL.md`** — see that document directly for the full,
  original threat-by-threat write-up this gap list draws from.

## 9. Free public demonstration option (Phase 9)

- **No external hosting deployment exists anywhere** — `docs/HOSTING_OPTIONS.md` is explicitly a
  recommendation document; no account was created, no platform selected, nothing deployed. A real
  pilot needs an actual, chosen, provisioned hosting environment, which does not exist today even
  at demo scale.
- **The public-demo quota safeguard (a per-organization `DeliveryRequest` cap) is a single
  mechanism**, not an exhaustive abuse-prevention system — no public self-service organization/
  courier signup surface exists yet for the corresponding per-IP/session abuse vectors to even
  apply to.
- **No screenshots, video, or static marketing page were produced** — the roadmap's own preferred
  "local package + video + static page" alternative to a hosted deployment is only half-built (the
  local package itself).
- **`config/settings/demo.py` has never been exercised by the automated test suite** (0% coverage) —
  verified only by manual inspection of its resolved settings values in this development
  environment.

## 10. Cross-cutting gaps (span multiple phases)

- **PostGIS/spatial indexing was deliberately deferred in every phase that touched geography.**
  `Facility.latitude`/`longitude` are plain `DecimalField`s (Phase 1), confirmed unchanged through
  Phase 9 by this session's own grep of `apps/facilities/models.py`. Every distance calculation in
  this codebase (`apps.deliveries.pricing`, `apps.dispatch.sla`, `apps.couriers.eligibility`'s zone
  matching) is either haversine straight-line math or plain FK-equality zone matching — never a real
  spatial query, index, or geofence. `docs/ARCHITECTURE_AND_DATA_MODEL.md`'s own architecture
  diagram names PostGIS; the actual codebase has never used it for anything.
- **No `RoutingProvider`, `PaymentProvider`, `BackgroundCheckProvider`, `ObjectStorageProvider`, or
  `TemperatureSensorProvider` exists as an actual Python type anywhere in this codebase.** Confirmed
  by grep this session: only `NotificationProvider` (Phase 7) was ever built as a real `Protocol`
  with concrete implementations. The other five are referenced only in docstrings/architecture
  documentation as forward-looking concepts — `RoutingProvider` in particular has **zero** mentions
  anywhere in application code, not even a docstring nod. See
  `docs/PILOT_READINESS/PROVIDER_ADAPTER_REQUIREMENTS.md` for the full, honest accounting of what
  exists vs. what the zero-cost policy's adapter-pattern claim actually delivered.
- **Test-database confidence is anchored on SQLite, with targeted, session-specific PostgreSQL
  verification, not continuous PostgreSQL CI.** Every quality gate in this repository's history
  (`ruff`/`mypy`/`pytest`/`makemigrations --check`) runs against `config.settings.test` (SQLite).
  Real PostgreSQL verification happened a handful of times, manually, against throwaway containers
  during Phase 4/8/9 development sessions (documented with real output each time) — not as a
  standing CI job. `.github/workflows/ci.yml` (confirmed by reading it this session) has no
  Postgres service container at all. Every SQLite-vs-PostgreSQL confidence caveat elsewhere in this
  document traces back to this one structural fact.
- **Coverage is consistently ~95%, never claimed as 100%, with no hard coverage gate anywhere** —
  stated honestly at the end of every phase; this is a reasonable prototype posture, not a gap in
  itself, but is noted here since a real pilot's own quality bar may want a hard threshold.
- **No load/soak/performance testing exists anywhere in this codebase's history** — every
  concurrency claim in this document is about *correctness under a small number of simultaneous
  writers*, never about throughput, latency, or behavior under realistic pilot-scale traffic.
