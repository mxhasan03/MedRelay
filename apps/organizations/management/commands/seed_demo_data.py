"""Deterministic synthetic demo data: 3 organizations, 8 facilities, NYC (Manhattan/Brooklyn).

Phase 1 subset only — couriers, deliveries, cargo, etc. are seeded in later
phases per docs/IMPLEMENTATION_ROADMAP.md. Every name/address/contact here is
obviously synthetic ("(Demo)" suffixes, fictional street names) per
docs/SECURITY_COMPLIANCE_BOUNDARIES.md's demo-data-prohibition rules — no
real business names, no real addresses, no diagnosis/lab/clinical/SSN/
insurance fields anywhere.

Safe to re-run: every write goes through `get_or_create`, and no randomness
is used anywhere, so re-running produces the same data instead of
duplicates or drift.

This command lives in `apps.organizations` (the tenancy-root app) rather
than a new top-level `apps.audit`/`scripts` location because most of what it
seeds — organizations, memberships, and their users — is core tenancy data;
it additionally imports `apps.facilities` models to seed the Phase 1
facility subset in the same deterministic pass, which is an intentional,
narrow exception to "apps stay decoupled" for a data-seeding command (not a
model/view import), since facilities already depend on organizations via
`Facility.organization`.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import InternalRole, InternalRoleAssignment
from apps.facilities.models import (
    Borough,
    DayOfWeek,
    Facility,
    FacilityContact,
    FacilityReceivingRule,
    FacilityType,
    ServiceZone,
)
from apps.organizations.models import (
    CustomerRole,
    Organization,
    OrganizationMembership,
    OrganizationType,
)

User = get_user_model()

# Synthetic-only demo password shared by every seeded user. Not a real
# secret: used solely so a reviewer can log into the local/demo stack and
# exercise the tenant-scoped CRUD UI end to end. See docs/CURRENT_STATUS.md.
DEMO_PASSWORD = "MedRelayDemo!2026"  # pragma: allowlist secret

INTERNAL_USERS: list[dict[str, Any]] = [
    {
        "username": "ops_dispatcher",
        "first_name": "Dana",
        "last_name": "Dispatcher (Demo)",
        "role": InternalRole.DISPATCHER,
    },
    {
        "username": "ops_manager",
        "first_name": "Morgan",
        "last_name": "Operations Manager (Demo)",
        "role": InternalRole.OPERATIONS_MANAGER,
    },
    {
        "username": "ops_courier_reviewer",
        "first_name": "Casey",
        "last_name": "Courier Reviewer (Demo)",
        "role": InternalRole.COURIER_ONBOARDING_REVIEWER,
    },
    {
        "username": "ops_compliance",
        "first_name": "Riley",
        "last_name": "Compliance Reviewer (Demo)",
        "role": InternalRole.COMPLIANCE_REVIEWER,
    },
    {
        "username": "ops_support",
        "first_name": "Sam",
        "last_name": "Customer Support (Demo)",
        "role": InternalRole.CUSTOMER_SUPPORT,
    },
    {
        "username": "ops_finance",
        "first_name": "Jamie",
        "last_name": "Finance (Demo)",
        "role": InternalRole.FINANCE,
    },
    {
        "username": "ops_sysadmin",
        "first_name": "Taylor",
        "last_name": "System Administrator (Demo)",
        "role": InternalRole.SYSTEM_ADMINISTRATOR,
    },
]

ORG_DEFS: list[dict[str, Any]] = [
    {
        "slug": "northstar",
        "name": "NorthStar Diagnostics (Demo)",
        "org_type": OrganizationType.DIAGNOSTIC_LAB,
        "facilities": [
            {
                "name": "NorthStar Labs — Midtown Processing Center (Demo)",
                "facility_type": FacilityType.LAB_PROCESSING_SITE,
                "address_line1": "148 Fictional Ave",
                "postal_code": "10118",
                "borough": Borough.MANHATTAN,
                "latitude": "40.754900",
                "longitude": "-73.984000",
            },
            {
                "name": "NorthStar Labs — SoHo Draw Site (Demo)",
                "facility_type": FacilityType.LAB_DRAW_SITE,
                "address_line1": "212 Demo Broome St",
                "postal_code": "10012",
                "borough": Borough.MANHATTAN,
                "latitude": "40.723300",
                "longitude": "-74.001600",
            },
            {
                "name": "NorthStar Labs — Park Slope Draw Site (Demo)",
                "facility_type": FacilityType.LAB_DRAW_SITE,
                "address_line1": "77 Sample 7th Ave",
                "postal_code": "11217",
                "borough": Borough.BROOKLYN,
                "latitude": "40.672000",
                "longitude": "-73.977200",
            },
        ],
    },
    {
        "slug": "riverside",
        "name": "Riverside Urgent Care Group (Demo)",
        "org_type": OrganizationType.URGENT_CARE,
        "facilities": [
            {
                "name": "Riverside Urgent Care — Chelsea (Demo)",
                "facility_type": FacilityType.URGENT_CARE_SITE,
                "address_line1": "300 Fictional 8th Ave",
                "postal_code": "10001",
                "borough": Borough.MANHATTAN,
                "latitude": "40.744700",
                "longitude": "-74.003900",
            },
            {
                "name": "Riverside Urgent Care — SoHo (Demo)",
                "facility_type": FacilityType.URGENT_CARE_SITE,
                "address_line1": "88 Demo Spring St",
                "postal_code": "10012",
                "borough": Borough.MANHATTAN,
                "latitude": "40.724800",
                "longitude": "-74.002700",
            },
            {
                "name": "Riverside Urgent Care — Williamsburg (Demo)",
                "facility_type": FacilityType.URGENT_CARE_SITE,
                "address_line1": "410 Sample Bedford Ave",
                "postal_code": "11211",
                "borough": Borough.BROOKLYN,
                "latitude": "40.714500",
                "longitude": "-73.961500",
            },
        ],
    },
    {
        "slug": "bkpharmacy",
        "name": "Brooklyn Family Pharmacy Network (Demo)",
        "org_type": OrganizationType.PHARMACY,
        "facilities": [
            {
                "name": "Brooklyn Family Pharmacy — Park Slope Counter (Demo)",
                "facility_type": FacilityType.PHARMACY_COUNTER,
                "address_line1": "500 Fictional 5th Ave",
                "postal_code": "11215",
                "borough": Borough.BROOKLYN,
                "latitude": "40.667400",
                "longitude": "-73.982900",
            },
            {
                "name": "Brooklyn Family Pharmacy — Dumbo Counter (Demo)",
                "facility_type": FacilityType.PHARMACY_COUNTER,
                "address_line1": "20 Demo Front St",
                "postal_code": "11201",
                "borough": Borough.BROOKLYN,
                "latitude": "40.703200",
                "longitude": "-73.988700",
            },
        ],
    },
]

CUSTOMER_ROLE_TITLES: dict[str, str] = {
    CustomerRole.OWNER: "Owner",
    CustomerRole.ADMINISTRATOR: "Administrator",
    CustomerRole.REQUESTER_DISPATCHER: "Requester-Dispatcher",
    CustomerRole.BILLING_MANAGER: "Billing Manager",
    CustomerRole.COMPLIANCE_REVIEWER: "Compliance Reviewer",
    CustomerRole.READ_ONLY_AUDITOR: "Read-Only Auditor",
}


class Command(BaseCommand):
    help = (
        "Seed deterministic synthetic NYC demo data: 3 healthcare organizations and 8 facilities "
        "across Manhattan/Brooklyn (Phase 1 subset — couriers/deliveries come in later phases). "
        "Safe to re-run: uses get_or_create throughout, never generates random data."
    )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        zones = self._seed_service_zones()
        internal_count = self._seed_internal_users()
        org_count, facility_count, membership_count = self._seed_organizations(zones)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {org_count} organizations, {facility_count} facilities, "
                f"{membership_count} customer-org memberships, {internal_count} internal-staff "
                f"users. Demo login password for every seeded user: {DEMO_PASSWORD!r} "
                "(synthetic, not a real secret)."
            )
        )

    def _seed_service_zones(self) -> dict[str, ServiceZone]:
        manhattan, _ = ServiceZone.objects.get_or_create(
            name="Manhattan Core (Demo)",
            defaults={
                "borough": Borough.MANHATTAN,
                "description": "Synthetic demo coverage area for Manhattan facilities.",
            },
        )
        brooklyn, _ = ServiceZone.objects.get_or_create(
            name="Brooklyn North (Demo)",
            defaults={
                "borough": Borough.BROOKLYN,
                "description": "Synthetic demo coverage area for Brooklyn facilities.",
            },
        )
        return {Borough.MANHATTAN: manhattan, Borough.BROOKLYN: brooklyn}

    def _seed_internal_users(self) -> int:
        count = 0
        for entry in INTERNAL_USERS:
            user, created = User.objects.get_or_create(
                username=entry["username"],
                defaults={
                    "first_name": entry["first_name"],
                    "last_name": entry["last_name"],
                    "email": f"{entry['username']}@medrelay.demo",
                    "is_internal_staff": True,
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
            InternalRoleAssignment.objects.get_or_create(
                user=user, defaults={"role": entry["role"]}
            )
            count += 1
        return count

    def _seed_organizations(self, zones: dict[str, ServiceZone]) -> tuple[int, int, int]:
        org_count = 0
        facility_count = 0
        membership_count = 0
        for org_def in ORG_DEFS:
            organization, _ = Organization.objects.get_or_create(
                name=org_def["name"], defaults={"org_type": org_def["org_type"]}
            )
            org_count += 1

            for role in CustomerRole.values:
                username = f"{org_def['slug']}_{role}"
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "first_name": org_def["slug"].title(),
                        "last_name": f"{CUSTOMER_ROLE_TITLES[role]} (Demo)",
                        "email": f"{username}@medrelay.demo",
                    },
                )
                if created:
                    user.set_password(DEMO_PASSWORD)
                    user.save(update_fields=["password"])
                OrganizationMembership.objects.get_or_create(
                    user=user, organization=organization, defaults={"role": role}
                )
                membership_count += 1

            for facility_def in org_def["facilities"]:
                facility, _ = Facility.objects.get_or_create(
                    organization=organization,
                    name=facility_def["name"],
                    defaults={
                        "facility_type": facility_def["facility_type"],
                        "address_line1": facility_def["address_line1"],
                        "postal_code": facility_def["postal_code"],
                        "borough": facility_def["borough"],
                        "latitude": facility_def["latitude"],
                        "longitude": facility_def["longitude"],
                        "service_zone": zones[facility_def["borough"]],
                        "access_instructions": (
                            "Ring bell at demo loading entrance; ask for courier drop-off desk."
                        ),
                        "verification_requirements": (
                            "Photo ID check-in at front desk (synthetic operational rule)."
                        ),
                    },
                )
                facility_count += 1

                site_label = facility.name.split("—")[-1].replace("(Demo)", "").strip()
                FacilityContact.objects.get_or_create(
                    facility=facility,
                    name=f"Demo Contact — {site_label}",
                    defaults={
                        "title": "Site Coordinator (Demo)",
                        "phone": "555-0100",
                        "email": "demo.contact@example.com",
                        "is_primary": True,
                    },
                )

                for day in range(5):  # Monday-Friday
                    FacilityReceivingRule.objects.get_or_create(
                        facility=facility,
                        day_of_week=day,
                        defaults={
                            "opens_at": datetime.time(8, 0),
                            "closes_at": datetime.time(18, 0),
                            "same_day_cutoff_time": datetime.time(16, 0),
                        },
                    )
                for day in (DayOfWeek.SATURDAY, DayOfWeek.SUNDAY):
                    FacilityReceivingRule.objects.get_or_create(
                        facility=facility,
                        day_of_week=day,
                        defaults={"is_closed": True},
                    )

        return org_count, facility_count, membership_count
