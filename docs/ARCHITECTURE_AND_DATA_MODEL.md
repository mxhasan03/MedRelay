# Architecture and Data Model

## 1. Logical architecture

```text
Customer Portal        Courier PWA        Operations Console       Recipient Link
       \                    |                    |                       /
        \---------------- Django ASGI Application --------------------/
                              |
        ----------------------------------------------------------------
        | Accounts | Organizations | Facilities | Couriers | Cargo     |
        | Deliveries | Dispatch | Custody | Tracking | Incidents       |
        | Temperature | Notifications | Billing | Reporting | Audit    |
        ----------------------------------------------------------------
                              |
              PostgreSQL + PostGIS / Valkey / Celery
                              |
           Local routing, email, object storage, mock adapters
```

## 2. Multi-tenancy

Use a shared database with explicit `organization_id` scoping. Every customer-owned entity must include an organization relationship. Central operations users may access multiple organizations through explicit permission checks.

Required protections:

- queryset scoping by tenant
- object-level permission tests
- no organization ID accepted blindly from clients
- audit events for sensitive access and mutations
- automated cross-tenant isolation tests

## 3. Main entities

### Identity and organizations

- `User`
- `Organization`
- `OrganizationMembership`
- `Role`
- `Facility`
- `FacilityContact`
- `FacilityReceivingRule`
- `ServiceZone`

### Couriers

- `CourierProfile`
- `CourierStatus`
- `CourierCredential`
- `TrainingRecord`
- `Vehicle`
- `Equipment`
- `CargoAuthorization`
- `CourierAvailability`
- `CourierLocationPing`
- `CourierPerformanceSnapshot`

### Cargo and packages

- `CargoClass`
- `CargoPolicy`
- `TemperatureProfile`
- `Package`
- `PackageIdentifier`
- `PackagingAttestation`
- `PackageConditionCheck`

### Delivery and dispatch

- `DeliveryRequest`
- `DeliveryStop`
- `DeliveryStatusTransition`
- `DeliveryAssignment`
- `JobOffer`
- `DispatchRecommendation`
- `DispatchOverride`
- `RoutePlan`
- `RouteLeg`
- `SLAProfile`

### Custody, proof, and incidents

- `CustodyEvent`
- `ProofOfPickup`
- `ProofOfDelivery`
- `RecipientVerification`
- `TemperatureReading`
- `TemperatureExcursion`
- `Incident`
- `IncidentAction`
- `ReturnResolution`

### Commercial and system

- `PricingRule`
- `Quote`
- `Invoice`
- `InvoiceLine`
- `Notification`
- `WebhookDelivery`
- `AuditEvent`
- `ExportJob`

(NOTE: None of these models are built in Phase 0. This section is here so CLAUDE.md and docs/CURRENT_STATUS.md can accurately describe what's coming later. Do not build these models now.)

## 5. State machine invariants

- delivered deliveries cannot return to in-transit without an appended correction/incident workflow
- pickup requires assignment and package verification
- delivery requires pickup/custody acceptance
- temperature-controlled assignment requires courier authorization and equipment
- incident hold blocks completion until an authorized resolution
- cancellation after pickup requires return or authorized exception handling
- user correction never deletes prior custody history
- duplicate idempotency keys cannot create duplicate delivery requests/events

## 9. Idempotency and concurrency

- require `Idempotency-Key` for create/transition endpoints
- use database transactions and row locks for assignment/state transitions
- use version fields/ETags for conflicting edits
- make Celery tasks idempotent
- deduplicate notifications and exports
