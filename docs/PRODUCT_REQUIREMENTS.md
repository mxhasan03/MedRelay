# Product Requirements Document

## 1. Product summary

MedRelay is a managed B2B healthcare logistics platform for New York City. It connects approved healthcare organizations with a closed network of vetted couriers for scheduled, same-day, and STAT transportation of approved medical items.

The platform combines four capabilities:

1. Customer delivery requests and recurring routes
2. Courier qualification, availability, and mobile job execution
3. Dispatcher-assisted assignment and operational control
4. Digital chain of custody, proof of delivery, incident handling, and reporting

The product is not a patient transportation service and is not an unrestricted consumer gig marketplace.

## 2. Pilot scope

### Geography

- Controlled Manhattan-Brooklyn service zone
- No routine Long Island, Westchester, New Jersey, or airport delivery in the prototype
- All timestamps stored in UTC and displayed in `America/New_York`

### Operating hours

- Weekdays: 7:00 AM-8:00 PM
- Evenings/weekends: prebooked plus limited STAT support
- No full overnight on-demand promise in the initial product

### Customer organizations

The data model supports:

- clinics
- urgent-care centers
- diagnostic laboratories
- pharmacies
- hospitals/health systems
- home-health organizations

Initial sales/use-case priority:

1. Clinics/urgent-care centers sending routine specimens to laboratories
2. Facility-to-facility documents and non-hazardous supplies
3. Sealed non-controlled pharmacy-prepared medication delivery after workflow review

### Delivery modes

- `SCHEDULED`: recurring or prebooked route
- `SAME_DAY`: customer-selected pickup/delivery window
- `STAT`: rapid assignment plus active SLA monitoring; not emergency medical response

## 3. Cargo classes

### Class 1 - Documents and non-hazardous supplies

Examples: sealed records, PPE, test kits, small devices, equipment parts, and non-hazardous supplies.

### Class 2 - Approved routine specimens

Only customer-attested, properly classified, packaged, sealed, and labeled routine specimens supported by written platform policy.

### Class 3 - Sealed non-controlled prescription medication

Pharmacy-prepared medication only. The pharmacy/facility remains responsible for lawful dispensing, packaging, labeling, and release.

### Temperature capabilities

- ambient
- refrigerated

Frozen cargo is deferred.

### Explicitly excluded

- patient transportation
- Category A infectious substances
- controlled substances
- human organs
- radioactive material
- regulated medical waste
- loose sharps
- unsealed specimens
- specialized blood products
- emergency-response cargo
- air shipments
- courier packaging/repacking

## 4. User groups and roles

### Customer organization roles

- organization owner
- administrator
- requester/dispatcher
- billing manager
- compliance reviewer
- read-only auditor

### Courier roles

- applicant
- approved courier
- suspended courier
- inactive courier

### Internal operations roles

- dispatcher
- operations manager
- courier onboarding reviewer
- compliance reviewer
- customer support
- finance
- system administrator

### Recipient

A recipient uses a short-lived secure link or PIN flow and does not require a full account in Version 1.

## 5. Customer portal requirements

### Dashboard

Show:

- active deliveries
- unassigned or awaiting-confirmation deliveries
- delayed/at-risk deliveries
- recent completed deliveries
- upcoming recurring routes
- service-level metrics

### Delivery request wizard

Required fields:

- pickup facility
- destination facility/home address
- pickup window
- required delivery time
- service level
- cargo class
- package count
- approximate dimensions/weight
- temperature requirement
- sender contact
- recipient contact/role
- packaging/classification attestation
- recipient verification method
- facility instructions

The request must block dispatch when required cargo or packaging information is missing.

### Recurring routes

Support:

- daily/weekly recurrence
- route start/end dates
- holiday exceptions
- multiple stops
- operations approval
- pause/resume

### Delivery tracking

Show:

- status timeline
- ETA
- courier identity appropriate to role
- active location after pickup when permitted
- SLA countdown
- exceptions
- custody events

### Reports

Support exports for:

- delivery summary
- custody timeline
- pickup/delivery proof
- incident summary
- on-time performance
- invoice summary

All exports use synthetic data in the prototype.

## 6. Courier PWA requirements

### Onboarding profile

- identity-review status placeholder
- driver-license status
- vehicle
- insurance status
- training records
- equipment
- cargo authorizations
- credential expirations

No real background-check provider is integrated in the zero-cost prototype.

### Availability

- online/offline
- shift availability
- current service zone
- current capacity

### Job offers

Show only eligible jobs based on hard eligibility rules. Courier can accept or reject. Legitimate cargo/safety rejection must be recordable.

### Active delivery

- navigation/routing summary
- pickup instructions
- cargo handling boundary
- scan package
- condition/seal checklist
- sender PIN/signature
- start transport
- destination instructions
- recipient PIN/signature
- incident reporting
- offline event queue

### Privacy

Courier sees only the minimum operational information needed for the task.

## 7. Operations control center

### Control tower

Show:

- live/last-known courier locations
- unassigned deliveries
- offered/accepted assignments
- at-risk deadlines
- incidents
- temperature alerts
- facility wait time
- expiring courier credentials

### Dispatch board

- eligibility-filtered courier candidates
- explainable assignment score
- manual assignment/reassignment
- reason-required overrides
- offer expiration
- SLA-feasibility warning

### Incident console

- incident category/severity
- current delivery hold
- response checklist
- customer notifications
- return-to-sender or alternate-destination resolution
- courier suspension/review action
- append-only event history

## 8. Recipient experience

- short-lived tracking link
- ETA/status
- masked communication placeholder
- recipient PIN confirmation
- signature option
- no unattended drop-off unless cargo/customer policy explicitly allows it

## 9. Delivery state machine

Primary states:

1. `DRAFT`
2. `SUBMITTED`
3. `VALIDATION_REQUIRED`
4. `READY_FOR_DISPATCH`
5. `OFFERED`
6. `ASSIGNED`
7. `COURIER_EN_ROUTE_TO_PICKUP`
8. `AT_PICKUP`
9. `PICKED_UP`
10. `IN_TRANSIT`
11. `AT_DESTINATION`
12. `DELIVERED`

Exception/terminal states:

- `REJECTED`
- `CANCELLED`
- `INCIDENT_HOLD`
- `RETURNING`
- `RETURNED`
- `FAILED`

All transitions must be explicit, validated, authorized, and recorded as append-only events.

## 10. Chain of custody

Required event types:

- request created
- package prepared/attested
- courier assigned
- courier arrived
- pickup scan
- condition verified
- custody accepted
- route started
- facility arrival
- recipient verified
- delivery scan
- custody transferred
- delivery completed
- incident opened/updated/resolved
- return initiated/completed
- correction appended

Each event stores:

- event ID
- delivery ID
- event type
- actor type/ID
- occurred-at timestamp
- recorded-at timestamp
- location when appropriate
- device/session metadata
- structured payload
- previous-event hash and current-event hash for tamper evidence

Corrections append new events and never overwrite originals.

## 11. Matching and dispatch rules

### Hard eligibility filters

A courier is ineligible when any required condition fails:

- account not active
- credential expired
- cargo authorization missing
- temperature capability missing
- vehicle/equipment incompatible
- outside service zone
- unavailable
- current capacity exceeded
- facility restriction not met
- SLA mathematically infeasible

### Explainable score for eligible couriers

Suggested weighted factors:

- ETA to pickup
- SLA slack
- reliability/on-time history
- route compatibility
- active workload
- facility familiarity
- toll/parking burden
- customer preference (non-binding)

The assignment service returns both a score and human-readable reasons.

### Dispatcher authority

Dispatchers can override recommendations but must record a reason. Overrides never bypass hard safety/authorization rules.

## 12. Temperature workflow

- requirement attached to package/delivery
- sender confirms prepared packaging
- eligible courier/equipment required
- indicator/logger placeholder
- readings/events attached to custody timeline
- excursion opens an incident and may place delivery on hold
- no claim of validated cold-chain compliance in the prototype

## 13. Incident categories

- leak/spill
- broken seal
- package damage
- temperature excursion
- vehicle accident
- courier injury/exposure
- lost package
- incorrect recipient
- wrong destination
- missed SLA
- recipient unavailable
- suspected tampering

Severe incidents suspend normal completion until an authorized resolution is recorded.

## 14. Pricing and billing prototype

The demo quote engine may calculate:

- base fee
- distance/time estimate
- service-level surcharge
- cargo/equipment surcharge
- toll estimate
- wait-time placeholder
- after-hours surcharge
- return-trip fee

Use synthetic configurable rules only. Do not connect a real payment processor.

Support:

- quote preview
- internal invoice records
- CSV/PDF-like HTML export
- payment-status mock

## 15. Notification prototype

Use:

- in-app notifications
- local Mailpit email
- logged/simulated SMS events

Do not require a paid SMS or email provider.

## 16. Non-functional requirements

- multi-tenant isolation
- role-based access control
- MFA-ready; TOTP supported for privileged users
- timezone-aware datetimes
- append-only audit/custody events
- accessible responsive UI
- English-first with translation-ready strings; Spanish later
- offline-capable courier event queue
- deterministic seed/demo mode
- no real PHI or real delivery operations
- strong input validation and file-size limits
- idempotent API operations
- optimistic concurrency or version checks for assignment/state transitions
