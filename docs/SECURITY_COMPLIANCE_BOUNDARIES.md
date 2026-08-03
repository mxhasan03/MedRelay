# Security and Compliance Boundaries

## 1. Required disclaimer

Every demo environment and relevant documentation must state:

> This is a software prototype using synthetic data. It is not certified or approved for real medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, employment, or other legal compliance.

## 2. Data minimization

Do not create fields for diagnoses, laboratory results, clinical notes, medication indications, social security numbers, insurance identifiers, or full patient records.

Preferred operational references:

- delivery ID
- package barcode
- accession/order reference
- organization/facility IDs
- authorized operational contacts

## 3. Demo-data prohibition

- no real patient information
- no real prescription information
- no real courier identity documents
- no real medical shipment labels
- no real customer contracts
- no secrets or credentials in Git

## 4. Authentication and access

- secure password hashing through Django
- TOTP MFA for privileged roles when enabled
- session security and CSRF protection
- rate limiting for public/recipient endpoints
- role and tenant checks on every sensitive operation
- short-lived signed recipient tokens
- never expose courier/customer personal contact data unnecessarily

## 5. Encryption and secrets

- HTTPS required for any hosted demo
- database encryption-at-rest is an infrastructure concern for future pilot deployment
- local secrets loaded through environment variables
- `.env` gitignored; `.env.example` contains names only
- GitHub Actions secrets only for CI-required credentials
- automated secret scan in CI

## 6. Auditability

Record:

- authentication events
- role/membership changes
- facility changes
- delivery state transitions
- assignment overrides
- custody events
- incident actions
- export creation
- sensitive record access where practical

Audit/custody records are append-only at the application level and protected with database permissions and tamper-evident hashes.

## 7. Safety rules represented in software

- sender classification/packaging attestation required
- couriers cannot open/repack cargo
- authorization/equipment hard gates
- missing cargo classification blocks dispatch
- temperature requirements hard gate assignment
- incident hold blocks completion
- controlled substances and prohibited cargo rejected
- STAT language never implies emergency medical response

## 8. Professional review gates before real operation

A real pilot requires independent review of:

- healthcare privacy and business-associate status
- customer/business-associate contracts
- specimen/infectious-substance eligibility and packaging
- pharmacy medication delivery rules
- New York worker classification
- insurance and vehicle requirements
- background-check consent/process
- incident/exposure plan
- data retention
- production hosting/security

Software implementation does not replace these reviews.
