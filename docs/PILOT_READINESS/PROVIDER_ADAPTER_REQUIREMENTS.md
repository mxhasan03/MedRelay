# Real-Provider Adapter Requirements

> This is a software prototype using synthetic data. It is not certified or approved for real
> medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other
> legal compliance.

`docs/TECH_STACK_AND_ZERO_COST_POLICY.md` section 4 and
`docs/ARCHITECTURE_AND_DATA_MODEL.md` section 7 name six adapter interfaces every external
capability should go through: `RoutingProvider`, `GeocodingProvider` (referred to in the task as a
distinct interface; this codebase's actual naming only ever discusses geocoding as part of routing —
see section 1 below), `NotificationProvider`, `ObjectStorageProvider`, `PaymentProvider`,
`BackgroundCheckProvider`, and `TemperatureSensorProvider`. This document states, for each, exactly
what exists in the code today (file-cited), what a real `LIVE` implementation would need, and — per
this session's own direct inspection of the code, not an assumption — whether the adapter-pattern
architecture actually would let a real provider be swapped in without rearchitecting.

**Headline finding, stated up front:** only **one** of these six — `NotificationProvider` — was
ever built as an actual Python `Protocol` with concrete implementations (Phase 7). The other five
are real, working *behaviors* (synthetic routing math, mock payment status, placeholder background-
check fields, inline signature storage, simulated temperature readings) but are **not** wrapped in
any formal adapter interface anywhere in the codebase — confirmed by grepping the full source tree
in this session. `RoutingProvider` in particular has zero mentions anywhere in application code, not
even a docstring reference. This is a materially more honest characterization than "the adapter
pattern is in place and just needs a live implementation swapped in" — for five of six capabilities,
the interface itself does not exist yet, only the behavior it would eventually sit behind.

---

## 1. `RoutingProvider` / `GeocodingProvider`

**What exists today:** No adapter interface of any kind. Distance/ETA is computed directly and
synchronously wherever needed: `apps.deliveries.pricing.estimate_distance_km` (haversine
straight-line distance between two facilities' stored `Decimal` `latitude`/`longitude`, with a
flat 5.0 km fallback if either coordinate is missing) and `apps.dispatch.sla.compute_sla_estimate`
(reuses the same haversine function for transit time, plus a small fixed set of synthetic
zone-match minute tiers for "ETA to pickup," since there is no courier-location signal fed into
dispatch scoring at all). Geocoding does not exist as a capability at all — facility coordinates are
manually entered/seeded, never resolved from an address by any code path.

**What a real `LIVE` implementation would need:** A real routing engine (self-hosted OSRM against
OpenStreetMap extracts, per `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`'s original intent, or a paid
routing API if the zero-cost constraint is later lifted for a real pilot) capable of real
road-network distance/time, ideally with traffic awareness for STAT-tier SLA accuracy; a geocoding
service (self-hosted Nominatim against OSM data, or a paid geocoding API) to resolve a typed address
into coordinates at facility-creation time, since today every facility's coordinates must be
supplied directly.

**Adapter-pattern verdict:** **Not verifiable — there is no interface to verify.** Because no
`RoutingProvider` protocol/class exists, there is nothing to "swap a live implementation into."
Introducing real routing would mean writing this adapter for the first time, then replacing the two
call sites above (`estimate_distance_km`, `compute_sla_estimate`) with calls through it — a
real, if bounded, engineering task, not a drop-in swap, because these two functions currently *are*
the implementation, not callers of an abstraction.

## 2. `NotificationProvider` — the one real adapter in this codebase

**What exists today:** `apps.notifications.providers.NotificationProvider` is a genuine Python
`Protocol` (`apps/notifications/providers.py`), with a uniform `ProviderResult` return shape
(`provider_name`, `mode` (`LOCAL`/`MOCK`), timestamp, correlation ID, `source`/`version`, `success`,
`detail`, `warnings`). Two concrete implementations satisfy it: `EmailNotificationProvider`
(`mode=LOCAL` — a genuine SMTP call via Django's `EMAIL_BACKEND`, landing in Mailpit locally) and
`SimulatedSmsProvider` (`mode=MOCK` — makes no network call of any kind, ever; only persists a local
`SmsLogEntry` row). Both are called from `apps.notifications.services.send_email_notification`/
`send_sms_notification`.

**Spot-checked against the actual code this session** (per the task's own instruction not to just
assert the adapter-pattern claim): `apps/notifications/services.py` **directly instantiates**
`EmailNotificationProvider()`/`SimulatedSmsProvider()` inline in each function
(`provider = EmailNotificationProvider()` at line 95; `provider = SimulatedSmsProvider()` at line
129) — there is **no settings-driven factory or dependency-injection point today** that would let a
real `LIVE` SMS provider be selected without touching this file. This is an important, honest
nuance: the *interface* (the `Protocol` and its `send(...)` call shape) genuinely would let a real
Twilio-or-equivalent-backed class satisfy the same contract with no change to any *caller* of
`send_sms_notification` — but `send_sms_notification` itself would need a small, localized edit (or
a settings-based factory function introduced above it) to actually construct the real provider
instead of `SimulatedSmsProvider()`. This is a modest, bounded change (swap one line, or add one
factory function used in two places), genuinely **not** a rearchitecting — but it is not literally
"zero code change to add a real provider" either, and this document states that precisely rather
than rounding up to "fully pluggable."

**What a real `LIVE` implementation would need:**
- **SMS**: a real account with an SMS provider (Twilio or equivalent — explicitly prohibited as a
  *required* dependency of this repository today per the zero-cost policy, relevant only once a
  real pilot lifts that constraint with owner approval), webhook signature verification for
  delivery-receipt callbacks (this prototype's `SmsLogEntry` has no delivery-receipt/webhook
  callback mechanism at all today — it records "sent," never "delivered" or "failed" after the
  fact), phone-number validation/formatting, and opt-out/STOP-keyword compliance handling (a real
  regulatory requirement for commercial SMS, entirely unaddressed in this prototype since no real
  SMS is ever sent).
- **Email**: a real transactional email provider or a properly configured outbound mail server
  (SPF/DKIM/DMARC) instead of Mailpit's local capture-only behavior; bounce/complaint handling,
  neither of which exists today since no message here has ever left the local Docker network.
- **Webhooks** (`apps.notifications.services.record_webhook_delivery_attempt`): this is currently a
  deliberate no-op stub that performs zero HTTP calls (confirmed: no `requests`/`urllib` import
  anywhere in `apps.notifications`) — a real implementation needs an actual outbound HTTP sender,
  request-signing (HMAC) so receiving customer systems can verify authenticity, retry/backoff logic,
  and an explicit egress-control/allowlist policy (this was a deliberate SSRF-avoidance choice per
  Phase 7's own design decision, not an oversight — extending the stub in place was explicitly
  judged the wrong move by that phase's own documentation).

**Adapter-pattern verdict:** **Substantially confirmed, with one caveat.** The `Protocol`-based
interface genuinely decouples callers from the concrete provider for the shape of a `send()` call —
a real `TwilioSmsProvider` class satisfying the same `Protocol` would need no changes to
`build_notification_payload`, `rendering.py`, or any calling view. The caveat is the missing
factory/DI seam noted above — a small, localized addition, not evidence the abstraction doesn't
work.

## 3. `ObjectStorageProvider`

**What exists today:** No adapter interface. `ProofOfPickup.signature_data_url`/
`ProofOfDelivery.signature_data_url` (`apps/custody/models.py`) store a base64-encoded PNG data URL
**inline as a `TextField`**, capped by an explicit length validator
(`apps.custody.validators.MAX_SIGNATURE_DATA_URL_LENGTH`) added in Phase 8. The model's own
docstring explicitly names `ObjectStorageProvider` as "the right home for this in a real
deployment," confirming this was a deliberate, documented placeholder, not an oversight.

**What a real `LIVE` implementation would need:** A real S3-compatible object store (self-hosted
MinIO, per `docs/TECH_STACK_AND_ZERO_COST_POLICY.md`'s named zero-cost option, or a paid cloud
object store for a real pilot), a migration path moving existing inline base64 signature data out of
the database into object storage with the model gaining a storage-key/URL field instead of the raw
data, and an access-control layer (signed/expiring URLs) so signature images aren't broadly
world-readable once out of the database's own permission model.

**Adapter-pattern verdict:** **Not verifiable — there is no interface to verify**, same as
`RoutingProvider`. Introducing this would be a genuine, if bounded, migration (add the interface,
add a MinIO-backed implementation, migrate existing data, change the two capture functions
`capture_proof_of_pickup`/`capture_proof_of_delivery` to write through it) — not a drop-in swap.

## 4. `PaymentProvider`

**What exists today:** No adapter interface, and per `apps/billing/models.py`'s own docstring,
deliberately so: "there is no `PaymentProvider` adapter with a real implementation, and there never
should be one in this repository" (per CLAUDE.md's do-not-build list). `Invoice.payment_status` is a
plain, manually-set `PaymentStatus` enum field — nothing in this codebase has ever touched a real
card number, bank account number, or payment-processor API of any kind.

**What a real `LIVE` implementation would need, and the PCI-scope discussion this document is asked
to have:** This application should **never** touch raw card data directly. The correct architecture
for a real `PaymentProvider` is a hosted-fields/tokenization integration (e.g. a processor's own
client-side JS SDK that tokenizes card data in the customer's browser before it ever reaches this
application's servers) so that MedRelay's own infrastructure only ever handles an opaque payment
token/reference, keeping this application's PCI-DSS scope to the smallest practical category (SAQ
A or A-EP-equivalent, depending on integration method) rather than the much heavier scope of
handling raw cardholder data server-side. Given this pilot's likely B2B customer base (a handful of
healthcare organizations, not high-volume consumer card transactions — see
`docs/PILOT_READINESS/BUDGET_CHECKLIST.md` item 9), invoicing/ACH may be a simpler, lower-PCI-scope
starting point than card processing at all; that is a business decision, not a purely technical one.

**Adapter-pattern verdict:** **Not verifiable, and not attempted by design.** This is the one
capability in this list where the current absence of an adapter is a *policy* choice
(CLAUDE.md's do-not-build list), not merely an unbuilt convenience — a real implementation is
explicitly deferred to an actual pilot-authorization decision, consistent with this project's
scope boundary.

## 5. `BackgroundCheckProvider`

**What exists today:** No adapter interface. `IdentityReviewStatus`/`DriverLicenseStatus`/
`InsuranceStatus` (`apps/couriers/models.py`) are manually-set placeholder enums; one
`CourierCredentialType` choice is literally labeled "Background Check (Placeholder — No Real
Provider Integrated)" in the model's own choices list, confirmed verbatim in this session's
inspection. `CourierCredential.evidence_reference` is a short text field (a placeholder
filename/label), never a real uploaded document — there is no `FileField`/upload path anywhere in
`apps.couriers`.

**What a real `LIVE` implementation would need:** A real background-check vendor account (Checkr or
an equivalent FCRA-compliant provider — explicitly prohibited as a *required* dependency today),
a real applicant-consent-capture flow (a genuine legal requirement, see
`docs/PILOT_READINESS/LEGAL_COMPLIANCE_CHECKLIST.md` item 7 — not a software design choice), webhook
or polling-based status updates from the vendor as a check progresses, an adverse-action notice flow
if a check comes back unfavorably (an FCRA requirement, not optional), and secure document storage
for any uploaded identity documents (which would need to go through the same `ObjectStorageProvider`
gap noted in item 3, since no file-upload mechanism of any kind exists in `apps.couriers` today).

**Adapter-pattern verdict:** **Not verifiable — there is no interface, and no upload mechanism to
attach a real provider's evidence to even if one existed.** This is a larger lift than the other
"not yet built" adapters: it requires both the provider integration and a net-new file-upload
capability that has never existed in this app at all.

## 6. `TemperatureSensorProvider`

**What exists today:** No adapter interface. `apps.temperature.services.record_reading` is always
called with an already-known `temperature_c` value supplied by the caller — it never polls or
listens for a device. `apps/temperature/management/commands/simulate_temperature_readings.py` is an
honestly-labeled synthetic generator (a seedable random walk around a `TemperatureProfile`'s
min/max range, with a configurable excursion chance) — both the model and the command's own
docstrings state plainly this is simulated, never live sensor data, and no claim of validated
cold-chain compliance is made anywhere (`docs/PRODUCT_REQUIREMENTS.md` section 12).

**What a real `LIVE` implementation would need:** Real IoT temperature-logger hardware attached to
refrigerated transport equipment, a real ingestion mechanism (the devices' own vendor API, MQTT, or
a webhook the device firmware calls), and a mapping from a physical device ID to a
`Package`/`DeliveryRequest` so an incoming real reading lands on the correct delivery's
`TemperatureReading` history — none of which exists today, since `record_reading`'s single call
signature (delivery/package + a bare `temperature_c` float + optional source) has no device-identity
or device-authentication concept built in at all yet.

**Adapter-pattern verdict:** **Not verifiable — there is no interface.** Unlike `RoutingProvider`/
`ObjectStorageProvider` above, this one would also need a genuinely new *inbound* data path (a
device pushing data into this application, rather than this application calling out to a provider),
which is an architecturally different shape than every other adapter on this list and would need
its own design work, not just "write the adapter and swap it in."

---

## Summary table

| Provider | Real `Protocol`/class exists? | Current implementation | Real-provider lift |
|---|---|---|---|
| `RoutingProvider`/`GeocodingProvider` | No — zero mentions in code | Haversine math + zone-match tiers | Write the interface + OSRM/geocoding integration; replace two call sites |
| `NotificationProvider` | **Yes** (`apps/notifications/providers.py`) | `EmailNotificationProvider` (real local SMTP), `SimulatedSmsProvider` (no network call) | Add a real SMS class satisfying the same `Protocol`; add a factory/settings seam (currently hardcoded instantiation) |
| `ObjectStorageProvider` | No — docstring mention only | Inline base64 `TextField` | Write the interface + MinIO/cloud integration; migrate existing inline data |
| `PaymentProvider` | No — explicitly not built by policy | `PaymentStatus` manual mock field | Tokenized/hosted-fields integration; PCI-scope decision; policy authorization required first |
| `BackgroundCheckProvider` | No — docstring mention only | Manually-set placeholder enums, no upload path | Write the interface + vendor integration + net-new file-upload capability + consent/adverse-action flow |
| `TemperatureSensorProvider` | No — docstring mention only | Simulated random-walk generator | Write the interface + real device ingestion path (new inbound-data architecture, not just an outbound adapter) |

**Bottom line for the "does the adapter pattern genuinely let a real provider be swapped in without
rearchitecting" question this document was asked to confirm or refute:** for the one adapter that
was actually built (`NotificationProvider`), the answer is **yes, substantially** — real
Twilio-or-equivalent SMS, in particular, could satisfy the existing `Protocol` with no change to any
caller, modulo a small factory/DI seam that does not exist yet. For the other five, the honest
answer is that there is no existing interface to make this claim about at all — the zero-cost
policy's own architecture document named six adapters as the design goal, and this codebase actually
built one of them. That is not a failure of Phase 7's actual scope (which was explicit about
building `NotificationProvider` specifically), but it does mean "the adapter pattern is proven out
across the board" is not an accurate claim about this codebase as it stands today, and this document
corrects that impression for whoever reads it next.
