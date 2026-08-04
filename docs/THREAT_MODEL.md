# Threat Model

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

This is a real, specific threat model for MedRelay as actually built through Phase 8 — not generic
boilerplate. Each threat below names the concrete mitigation already in the codebase (by file
path), states its honest residual risk, and calls out what a real pilot review
(`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8) would need to independently verify before any
real operation. "Accepted risk for a demo prototype" below means exactly that — accepted for a
free, synthetic-data, single-developer-machine demo, not for a real deployment.

## 1. Tenant-isolation bypass

**Threat**: a customer-organization user (or a compromised session) views or modifies another
organization's facilities, delivery requests, invoices, or other tenant-owned data by manipulating
an object ID in a URL or form.

**Mitigations**:
- Every tenant-owned queryset is scoped through `apps.organizations.services.
  scope_queryset_to_user_orgs` (or a thin `for_user()` wrapper delegating to it) — never a raw,
  client-trusted filter. See that module's docstring for the "no organization ID accepted blindly
  from a client" rule.
- Every detail/update view performs an explicit object-level permission check
  (`can_view_organization`/`can_manage_organization`/`can_manage_facilities`/
  `can_create_delivery_requests`/`can_view_billing`/`can_export_reports`/`can_view_audit_log`) before
  returning a response, raising `PermissionDenied` (403) otherwise — e.g.
  `apps/organizations/views.py::OrganizationDetailView.get_object`,
  `apps/facilities/views.py`, `apps/deliveries/views.py`, `apps/billing/views.py`,
  `apps/audit/views.py::AuditEventListView.dispatch`.
- Internal staff get cross-org access only through named, reviewed allowlists
  (`CROSS_ORG_READ_ROLES`/`CROSS_ORG_MANAGE_ROLES`/`DISPATCH_ROLES`/`AUDIT_VIEWER_ROLES`/
  `BILLING_ROLES` in `apps/organizations/services.py`) — `user.is_internal_staff` alone grants
  nothing.
- Automated cross-tenant isolation tests exist at both the queryset layer
  (`apps/organizations/tests/test_services.py`) and the real-HTTP layer
  (`apps/organizations/tests/test_views.py`, `apps/deliveries/tests/test_views.py::
  test_cannot_view_other_org_delivery_request_via_http`, `apps/audit/tests/test_views.py`).

**Residual risk**: this is enforced per-view, not by a single global middleware/database-level
row-security policy (e.g. Postgres Row-Level Security) — a newly-added view that forgets to call
one of the permission helpers would be a real gap. A pilot review should audit every view for this
before real data is ever attached. **Accepted for a demo prototype.**

## 2. Recipient-link/PIN brute-force

**Threat**: an attacker guesses a recipient's delivery-confirmation PIN, or forges/brute-forces a
recipient tracking token, to view or falsely confirm someone else's delivery.

**Mitigations**:
- Tracking tokens are short-lived, cryptographically signed
  (`apps/recipient/tokens.py`, `django.core.signing.TimestampSigner`, 72-hour `max_age`) — not
  sequential/guessable IDs. Expired or tampered tokens are rejected (403/404) without ever granting
  access (`apps/recipient/views.py::RecipientTrackingView._resolve_or_reject`).
- PINs are never stored in plaintext — only a salted PBKDF2 hash
  (`apps.custody.services.generate_recipient_pin`, `django.contrib.auth.hashers.make_password`) —
  and are verified with a constant-time comparison (`check_password`).
- **Phase 8 (new)**: the PIN-verification POST and the token-resolution GET are both rate-limited
  (`apps/recipient/views.py`, `django-ratelimit`): 5 PIN attempts/minute per token, 10/minute per IP
  on the POST, 30/minute per IP on the GET, all backed by the same Valkey cache the rest of the app
  uses. A rejected request gets a real `429`
  (`apps.recipient.views.ratelimited_view`), not a silent pass-through, and the rejection response
  carries no information about whether the PIN was close to correct (see
  `apps/recipient/tests/test_rate_limiting.py::
  test_rate_limited_response_does_not_leak_pin_validity_information`). Before this phase, this
  endpoint had **no** rate limiting at all — a real, meaningful gap for a 4-6 digit PIN, now closed.

**Residual risk**: rate limiting is per-IP/per-token in an in-process/shared cache — a
distributed/rotating-IP attacker could still spread attempts across many source IPs to attack one
token's PIN at the per-token rate (5/minute), which for a 6-digit PIN (1,000,000 possibilities)
still takes an impractically long time (~139 days at 5/minute) but is not mathematically zero. A
real pilot should consider a hard per-token attempt cap (e.g. lock the PIN after N total failures)
rather than only a rolling rate limit. **Accepted for a demo prototype.**

## 3. Custody-chain tampering

**Threat**: a custody event (pickup, hand-off, delivery, incident) is altered or deleted after the
fact to hide what actually happened to a shipment.

**Mitigations**:
- `apps.custody.models.CustodyEvent` is a real, per-delivery SHA-256 hash chain
  (`apps.custody.hashing.compute_event_hash`) — each event's hash commits to the previous event's
  hash, so altering any historical event breaks every subsequent hash, detectable by
  `apps.custody.verification.verify_custody_chain`.
- Writes only ever go through `apps.custody.services.record_event`, which computes `sequence`/
  `previous_hash`/`current_hash` under a row lock on the parent `DeliveryRequest` — never
  `CustodyEvent.objects.create(...)` directly elsewhere.
- Corrections append (`correction_of` self-FK, `apps.custody.services.append_correction`); the
  original row is never edited.
- The same append-only pattern (ORM-level `save()`/`delete()` guards plus a custom queryset
  blocking bulk `update()`/`delete()`) is used for `apps.deliveries.models.DeliveryStatusTransition`
  and the new (Phase 8) `apps.audit.models.AuditEvent`.

**Honest limitation, stated plainly by the code itself**: this is **ORM-level** append-only
enforcement, not a database-level guarantee. A raw SQL statement issued outside the Django ORM (a
direct `psql` session, a compromised database credential, or a future code path that bypasses
`record_event`) could still mutate history — there is no Postgres `REVOKE UPDATE/DELETE` grant or
trigger backing this today. `apps/custody/models.py`'s own module docstring and
`apps/deliveries/models.py`'s `DeliveryStatusTransition` docstring both say this explicitly. A real
pilot's professional review (`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8) should add a
database-level guard (a restricted application role without `UPDATE`/`DELETE` grants on these
tables, or a Postgres trigger) before treating this chain as tamper-*proof* rather than
tamper-*evident*. **Accepted for a demo prototype** — the hash chain still turns any bypass
attempt into something detectable after the fact, which is the property this phase claims.

## 4. Notification data leakage

**Threat**: a notification (in-app, email via Mailpit, or the simulated SMS adapter) leaks a
contact's real name, phone number, address, PIN, or signature to the wrong audience or into a log.

**Mitigations**:
- Every notification payload is validated against an explicit allow-list of field names
  (`apps.notifications.payload.ALLOWED_NOTIFICATION_FIELDS`) before it is ever persisted or
  rendered — `apps.notifications.payload.validate_payload` raises loudly on anything not
  allow-listed, rather than silently stripping it (a stripped field could hide a bug; a loud
  rejection cannot). Rendering (`apps.notifications.rendering`) only ever reads from that
  validated, allow-listed payload — never arbitrary caller-supplied strings.
- The recipient tracking page similarly masks identity-adjacent data
  (`apps.recipient.services.build_masked_tracking_context` — courier identity is reduced to "Your
  assigned courier"/"A courier will be assigned soon", never a real name/phone; see
  `apps/recipient/tests/test_views.py`'s explicit `assert b"555-" not in response.content` check).

**Residual risk**: the allow-list is maintained by hand; a future field added to a notification
payload without updating `ALLOWED_NOTIFICATION_FIELDS` would fail loudly (safe direction), but a
field added *to* the allow-list without enough scrutiny could still leak something sensitive. **No
paid/production SMS or email provider is used** (Mailpit locally, a simulated in-repo SMS adapter —
`docs/TECH_STACK_AND_ZERO_COST_POLICY.md`), so there is no third-party data-processor exposure risk
in the demo today. **Accepted for a demo prototype.**

## 5. Courier location/tracking privacy

**Threat**: a courier's real-time location is exposed to parties who should not see it, or is
retained longer than operationally necessary.

**Mitigations**:
- `apps.tracking.models.CourierLocationPing` is written only through
  `apps.tracking.services.record_location_ping`, and location updates stop once a delivery reaches
  a terminal state (an explicit, tested acceptance criterion per that module).
- Location data is tied to a specific `DeliveryAssignment`, not exposed as a general "where is
  courier X right now" query — nothing in this codebase exposes raw courier coordinates to the
  recipient tracking page (`apps.recipient.services.build_masked_tracking_context` exposes only
  delivery status/timeline, never coordinates) or to any organization outside the internal ops
  dispatch console.
- Only internal ops roles allow-listed for dispatch (`apps.organizations.services.DISPATCH_ROLES`)
  reach the dispatch console where assignment/location context is visible at all.

**Residual risk**: there is no explicit data-retention/purge policy for
`CourierLocationPing` rows — they accumulate indefinitely in this prototype. A real pilot should
define a retention window (e.g. purge pings older than N days once a delivery is terminal) per
`docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8's "data retention" review gate. **Accepted for a
demo prototype** (synthetic couriers, synthetic coordinates, no real employee/contractor location
data).

## 6. Session and CSRF

**Threat**: session hijacking, cross-site request forgery, or clickjacking against any
authenticated view.

**Mitigations**:
- `django.middleware.csrf.CsrfViewMiddleware` is enabled (`config/settings/base.py`); every
  state-changing form in this codebase uses `{% csrf_token %}` (checked by the same
  view/integration tests that exercise those forms).
- `X_FRAME_OPTIONS = "DENY"` and `SECURE_REFERRER_POLICY = "same-origin"`
  (`config/settings/base.py`) mitigate clickjacking and cross-origin referrer leakage.
- `config/settings/prod.py` sets `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/
  `SECURE_SSL_REDIRECT`/HSTS for any real hosted deployment — `DEMO_MODE`'s dev settings
  deliberately do not force HTTPS-only cookies, since local `docker compose` demo access is plain
  HTTP by default (see `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 5: "HTTPS required for any
  hosted demo").
- Passwords are hashed with Django's default PBKDF2 hasher; no custom/weakened hasher is configured
  anywhere in `config/settings/`.
- **Phase 8 (new)**: TOTP MFA (`apps/accounts/mfa.py`) is available for privileged accounts
  (internal ops staff, customer-org owners/administrators —
  `apps.organizations.services.is_mfa_eligible`). Once a user enrolls, a genuine second factor is
  required before their session is established (`MedRelayLoginView`/`MfaVerifyView` — see
  `apps/accounts/tests/test_mfa.py` for a real login blocked/verified with a real generated TOTP
  code). MFA is opt-in, not mandatory for every demo account, per this phase's own documented scope
  decision.

**Residual risk**: MFA enrollment is optional, so most seeded demo accounts are single-factor.
Session fixation/rotation on privilege escalation (e.g. rotating the session key on MFA completion)
is handled by Django's default `auth_login()` behavior (which does rotate the session key), so no
additional gap beyond Django's own defaults. **Accepted for a demo prototype** — a real pilot should
decide whether MFA becomes mandatory for specific roles.

## 7. Other relevant surfaces

- **SQL injection**: all data access goes through the Django ORM with parameterized queries; no
  raw SQL string interpolation exists anywhere in `apps/`.
- **XSS**: Django's template auto-escaping is on by default and not disabled (no `|safe`/`mark_safe`
  usage on any user-supplied string in this codebase's templates).
- **Upload/input-size denial-of-service**: (Phase 8, new) `DATA_UPLOAD_MAX_MEMORY_SIZE`/
  `FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_NUMBER_FIELDS` (`config/settings/base.py`) cap
  request-body size and field count; the one base64-image-carrying field
  (`signature_data_url`) has an explicit, enforced length cap
  (`apps/custody/validators.py`) — see `docs/CURRENT_STATUS.md` "Phase 8" for the full audit.
- **Secret leakage**: `detect-secrets-hook` gates every commit (`.secrets.baseline`); `.env` is
  gitignored; no real credential has ever been committed (see `docs/CURRENT_STATUS.md`'s
  phase-by-phase secret-scan results).
- **Dependency/supply-chain risk**: every dependency is checked against an explicit allowlist
  (`python manage.py audit_cost`) — see `docs/COST_AUDIT.md`. This defends against *cost*
  surprises, not against a specific package being compromised upstream (no dependency-vulnerability
  scanning like `pip-audit`/Dependabot alerts is wired into CI today — a reasonable pilot-review
  addition).
- **Idempotency/concurrency**: `apps.dispatch.services.assign_delivery` uses
  `select_for_update()` + a partial unique constraint to guarantee exactly one winner under
  concurrent assignment attempts (`apps/dispatch/tests/test_concurrency.py`); courier action
  endpoints require an `Idempotency-Key` (`apps.couriers.idempotency`).

## 8. Explicitly out of scope for this threat model

Per `docs/SECURITY_COMPLIANCE_BOUNDARIES.md` section 8, none of the following have been reviewed
here and none are claimed:

- Healthcare privacy/business-associate status, specimen/infectious-substance packaging
  eligibility, pharmacy medication-delivery rules, NY worker classification, insurance/vehicle
  requirements, background-check consent/process, incident/exposure plans, data retention policy,
  or production hosting/security — all require independent professional review before any real
  pilot, and this document does not substitute for that review.
