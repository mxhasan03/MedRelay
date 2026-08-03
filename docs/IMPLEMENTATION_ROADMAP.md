# Implementation Roadmap

## Phase 0 - Repository foundation

Deliver:

- repository and branch strategy
- Django project skeleton
- modular app structure
- local compose stack: web, PostgreSQL/PostGIS, Valkey, worker, Mailpit
- pyproject/lock file
- CI: lint, type check, tests, migration check, cost audit, secret scan
- global demo/compliance disclaimer
- architecture/status documentation

Acceptance:

- one documented command starts the stack
- health endpoint works
- all quality gates pass
- no paid dependency

## Phase 1 - Identity, tenancy, facilities, and roles

Deliver: custom user model; organizations and memberships; customer/internal role system; facilities and service zones; tenant-scoped query/services; synthetic seed data; admin interface.

## Phase 2 - Cargo policy and delivery requests

Deliver: cargo classes/policies; temperature profiles; packages/attestations; delivery request wizard; scheduled/same-day/STAT; quote engine with synthetic pricing; validation and prohibited-cargo rules; recurring-route model.

## Phase 3 - Courier onboarding and eligibility

Deliver: courier profiles/status; credentials/training/vehicle/equipment; authorization levels; availability; eligibility engine; credential expiration warnings.

## Phase 4 - Dispatch and operations console

Deliver: dispatch recommendations; explainable score; job offers and expiration; assignment/reassignment; dispatcher override with reason; operations dashboard/map; SLA-risk rules.

## Phase 5 - Courier PWA and tracking

Deliver: mobile-first courier UI; accept/reject; pickup/delivery workflows; browser location updates; offline event queue; QR scanning plus manual fallback; active-delivery timeline.

## Phase 6 - Custody, proof, temperature, and incidents

Deliver: append-only custody events; sender/recipient PIN and signature prototype; package condition checklist; temperature readings/excursion simulation; incident console; return-to-sender flow; tamper-evident event chain verifier.

## Phase 7 - Notifications, recipient tracking, billing, and reports

Deliver: in-app notifications; Mailpit email; simulated SMS adapter; secure recipient link; invoice records; CSV/HTML exports; operational metrics.

## Phase 8 - UX, accessibility, security, and demo hardening

Deliver: unified design system; responsive interfaces; accessibility pass; TOTP MFA for privileged demo accounts; upload/input limits; rate limiting; audit viewer; backup/restore documentation; threat model.

## Phase 9 - Free public demonstration option

Deploy synthetic demo mode only, all providers mocked/local, no paid/card-required hosting.

## Phase 10 - Pilot readiness review, not automatic launch

Gap assessment, legal/compliance checklist, insurance/infra budget checklist, real-provider adapter requirements, go/no-go report. Do not connect real PHI/deliveries/payments/background checks/production comms without explicit owner approval.
