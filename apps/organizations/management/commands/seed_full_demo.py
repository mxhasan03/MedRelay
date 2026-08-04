"""Phase 9 comprehensive, end-to-end synthetic demo seed.

`seed_demo_data` (Phase 1) seeds only the tenancy subset: organizations,
facilities, and their users. Every later phase added a whole domain
(couriers, deliveries, dispatch, custody, temperature, incidents, billing)
that a demo visitor should actually be able to see and click through — this
command is what makes the free public demo (Phase 9,
docs/IMPLEMENTATION_ROADMAP.md) show a *coherent*, lived-in dataset instead
of an empty tenancy shell.

This command lives in `apps.organizations` (not a new top-level app) for the
same reason `seed_demo_data` does — see that command's module docstring —
plus a second, Phase-9-specific reason: it is explicitly one more narrow,
documented exception to "apps stay decoupled," this time for an
orchestration script that calls real cross-app *service functions*
(`apps.deliveries.services`, `apps.dispatch.services`,
`apps.couriers.services`, `apps.custody.services`, `apps.temperature.
services`, `apps.incidents.services`, `apps.billing.services`) rather than
poking at models directly — the same real state machine, hard-eligibility
gates, and custody hash chain a real user action would go through. This is
deliberate: a demo built by writing rows directly into the database would
not actually prove those mechanisms work, and would risk drifting out of
sync with them (e.g. a hand-built `DeliveryRequest` missing a required
custody event the state machine assumes exists). Every scenario below is
therefore created via the exact same functions
`apps/deliveries/views.py`/`apps/dispatch/views.py`/`apps/couriers/views.py`/
`apps/incidents/views.py`/`apps/billing/views.py` call.

## What this seeds (beyond `seed_demo_data`'s orgs/facilities/users)

- **Couriers with varied credential/authorization states**
  (`docs/IMPLEMENTATION_ROADMAP.md` Phase 9 "varied credential/authorization
  states"): a fully-approved refrigerated-capable Manhattan courier, a
  fully-approved ambient-only Brooklyn courier, an approved courier with a
  driver-license credential expiring soon (the "credential-expiration
  warning" scenario — see `flag_expiring_credentials`), an unreviewed
  applicant still mid-onboarding, and a suspended courier.
- **Delivery requests spanning different states**: one left at
  `READY_FOR_DISPATCH` (unassigned), one `ASSIGNED` (not yet advanced), one
  driven through the full courier/custody lifecycle to `DELIVERED` (proof of
  pickup, in-range temperature reading, recipient PIN verification, proof of
  delivery — a complete, real custody chain), one with a genuine
  **temperature excursion** (an out-of-range reading that
  `apps.temperature.services.record_reading` itself turns into a `SEVERE`
  incident and an `INCIDENT_HOLD`, left open as a live demo item), and one
  **recipient-unavailable return** driven all the way to `RETURNED` via a
  real incident + `apps.incidents.services.initiate_return`/
  `complete_return`.
- **At least one generated invoice** (`apps.billing.services.
  generate_invoice_for_delivery`) for the `DELIVERED` scenario.

## Idempotency (a documented simplification, not full re-seed safety)

`seed_demo_data`'s org/facility/user seeding and this command's courier
seeding are fully idempotent (`get_or_create` throughout, exactly like
`seed_demo_data`). The five delivery-lifecycle *scenarios*, however, are
built by driving a real, stateful lifecycle through several service calls
each — there is no honest way to "get_or_create" a multi-step lifecycle
without either replaying every intermediate transition (fragile, and not
what a real re-run should do) or silently skipping it. Instead, each
scenario is tagged with a stable marker string
(`SCENARIO_TAGS`) written into `DeliveryRequest.facility_instructions`, and
re-running this command skips (with a clear stdout message) any scenario
whose tagged row already exists, rather than erroring or duplicating it.
This means re-running this command is safe (never raises, never duplicates
a scenario) but will not "heal" a scenario that was left in a different
state than this command would have produced (e.g. by a demo visitor's own
interactions) — an explicit, honest limitation, not a silent one. Use
`reset_demo_data` (in this same app) to wipe and rebuild from a clean slate
if a fully deterministic dataset is required.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.billing.services import generate_invoice_for_delivery
from apps.cargo.models import (
    CargoClass,
    CargoClassCode,
    TemperatureProfile,
    TemperatureProfileCode,
)
from apps.couriers.models import (
    CargoAuthorization,
    CourierAvailability,
    CourierCredential,
    CourierCredentialStatus,
    CourierCredentialType,
    CourierProfile,
    CourierStatus,
    DriverLicenseStatus,
    Equipment,
    EquipmentType,
    IdentityReviewStatus,
    InsuranceStatus,
    Vehicle,
    VehicleType,
)
from apps.couriers.services import advance_delivery_status
from apps.custody.services import (
    capture_proof_of_delivery,
    capture_proof_of_pickup,
    generate_recipient_pin,
    verify_recipient_pin,
)
from apps.deliveries.models import DeliveryRequest, DeliveryStatus, RecipientVerificationMethod
from apps.deliveries.services import create_delivery_request, submit_delivery_request
from apps.deliveries.state_machine import transition_delivery_request
from apps.dispatch.services import assign_delivery
from apps.facilities.models import Facility, ServiceZone
from apps.incidents.models import IncidentCategory, IncidentResolutionType, IncidentSeverity
from apps.incidents.services import (
    complete_return,
    initiate_return,
    open_incident,
    resolve_incident,
)
from apps.organizations.management.commands.seed_demo_data import DEMO_PASSWORD
from apps.organizations.models import Organization
from apps.temperature.services import record_reading

User = get_user_model()

# Stable markers embedded in DeliveryRequest.facility_instructions so this
# command's five lifecycle scenarios can be detected on re-run without
# needing a dedicated "is this seed data" model field. See module docstring
# "Idempotency" section.
SCENARIO_TAGS: dict[str, str] = {
    "ready_for_dispatch": "[DEMO-SCENARIO:READY-FOR-DISPATCH]",
    "assigned": "[DEMO-SCENARIO:ASSIGNED]",
    "delivered_full_chain": "[DEMO-SCENARIO:DELIVERED-FULL-CHAIN]",
    "temperature_excursion": "[DEMO-SCENARIO:TEMPERATURE-EXCURSION]",
    "recipient_unavailable_return": "[DEMO-SCENARIO:RECIPIENT-UNAVAILABLE-RETURN]",
}

COURIER_DEFS: list[dict[str, Any]] = [
    {
        "username": "demo_courier_ana",
        "first_name": "Ana",
        "last_name": "Rivera (Demo Courier)",
        "status": CourierStatus.APPROVED,
        "identity_review_status": IdentityReviewStatus.APPROVED,
        "driver_license_status": DriverLicenseStatus.VALID,
        "insurance_status": InsuranceStatus.VALID,
        "zone_name": "Manhattan Core (Demo)",
        "refrigerated": True,
        "credential_expires_on": datetime.date(2027, 6, 1),
    },
    {
        "username": "demo_courier_ben",
        "first_name": "Ben",
        "last_name": "Okafor (Demo Courier)",
        "status": CourierStatus.APPROVED,
        "identity_review_status": IdentityReviewStatus.APPROVED,
        "driver_license_status": DriverLicenseStatus.VALID,
        "insurance_status": InsuranceStatus.VALID,
        "zone_name": "Brooklyn North (Demo)",
        "refrigerated": False,
        "credential_expires_on": datetime.date(2027, 6, 1),
    },
    {
        "username": "demo_courier_cara",
        "first_name": "Cara",
        "last_name": "Nguyen (Demo Courier)",
        "status": CourierStatus.APPROVED,
        "identity_review_status": IdentityReviewStatus.APPROVED,
        "driver_license_status": DriverLicenseStatus.VALID,
        "insurance_status": InsuranceStatus.VALID,
        "zone_name": "Manhattan Core (Demo)",
        "refrigerated": False,
        # Within flag_expiring_credentials' default 30-day window, but not
        # yet expired — the "credential-expiration warning" demo scenario.
        "credential_expires_on": None,  # computed relative to today, see _seed_couriers
    },
    {
        "username": "demo_courier_dee",
        "first_name": "Dee",
        "last_name": "Applicant (Demo Courier)",
        "status": CourierStatus.APPLICANT,
        "identity_review_status": IdentityReviewStatus.PENDING_REVIEW,
        "driver_license_status": DriverLicenseStatus.PENDING_REVIEW,
        "insurance_status": InsuranceStatus.NOT_SUBMITTED,
        "zone_name": None,
        "refrigerated": False,
        "credential_expires_on": None,
        "onboarding_only": True,
    },
    {
        "username": "demo_courier_eli",
        "first_name": "Eli",
        "last_name": "Suspended (Demo Courier)",
        "status": CourierStatus.SUSPENDED,
        "identity_review_status": IdentityReviewStatus.APPROVED,
        "driver_license_status": DriverLicenseStatus.VALID,
        "insurance_status": InsuranceStatus.VALID,
        "zone_name": "Manhattan Core (Demo)",
        "refrigerated": False,
        "credential_expires_on": datetime.date(2027, 6, 1),
    },
]


class Command(BaseCommand):
    help = (
        "Seed a comprehensive, deterministic end-to-end demo dataset (Phase 9): calls "
        "seed_demo_data for organizations/facilities/users, then adds couriers with varied "
        "credential/authorization states and five delivery requests spanning different "
        "lifecycle states (ready-for-dispatch, assigned, fully delivered with a real custody "
        "chain, a temperature excursion/incident, and a recipient-unavailable return), plus a "
        "generated invoice. Safe to re-run (see module docstring 'Idempotency')."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        call_command("seed_demo_data")

        couriers = self._seed_couriers()
        northstar = Organization.objects.get(name="NorthStar Diagnostics (Demo)")
        riverside = Organization.objects.get(name="Riverside Urgent Care Group (Demo)")
        bkpharmacy = Organization.objects.get(name="Brooklyn Family Pharmacy Network (Demo)")
        ops_dispatcher = User.objects.get(username="ops_dispatcher")

        created_scenarios = []
        if self._seed_ready_for_dispatch(riverside):
            created_scenarios.append("ready_for_dispatch")
        if self._seed_assigned(northstar, couriers["ben"], ops_dispatcher):
            created_scenarios.append("assigned")
        if self._seed_delivered_full_chain(riverside, couriers["ana"], ops_dispatcher):
            created_scenarios.append("delivered_full_chain (+ invoice)")
        if self._seed_temperature_excursion(northstar, couriers["ana"], ops_dispatcher):
            created_scenarios.append("temperature_excursion")
        if self._seed_recipient_unavailable_return(bkpharmacy, couriers["ben"], ops_dispatcher):
            created_scenarios.append("recipient_unavailable_return")

        if created_scenarios:
            self.stdout.write(
                self.style.SUCCESS(f"Seeded new demo scenarios: {', '.join(created_scenarios)}.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "All Phase 9 demo scenarios already exist — nothing new to seed "
                    "(re-run is a no-op; see module docstring 'Idempotency')."
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo login password for every seeded user (couriers included): "
                f"{DEMO_PASSWORD!r} (synthetic, not a real secret)."
            )
        )

    # -- Couriers ----------------------------------------------------------

    def _seed_couriers(self) -> dict[str, CourierProfile]:
        zones = {z.name: z for z in ServiceZone.objects.all()}
        cargo_classes = {c.code: c for c in CargoClass.objects.all()}
        result: dict[str, CourierProfile] = {}
        today = timezone.localdate()

        for entry in COURIER_DEFS:
            user, created = User.objects.get_or_create(
                username=entry["username"],
                defaults={
                    "first_name": entry["first_name"],
                    "last_name": entry["last_name"],
                    "email": f"{entry['username']}@medrelay.demo",
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])

            zone = zones.get(entry["zone_name"]) if entry["zone_name"] else None
            profile, _ = CourierProfile.objects.get_or_create(
                user=user,
                defaults={
                    "status": entry["status"],
                    "identity_review_status": entry["identity_review_status"],
                    "driver_license_status": entry["driver_license_status"],
                    "insurance_status": entry["insurance_status"],
                    "home_service_zone": zone,
                    "phone": "555-0170",
                    "approved_at": (
                        timezone.now() if entry["status"] == CourierStatus.APPROVED else None
                    ),
                },
            )
            key = entry["username"].removeprefix("demo_courier_")
            result[key] = profile

            if entry.get("onboarding_only"):
                # An applicant mid-onboarding has no credentials/vehicle/
                # equipment/authorization/availability yet — that is exactly
                # the varied state being demonstrated here, not an omission.
                continue

            expires_on = entry["credential_expires_on"]
            if expires_on is None:
                # The "credential-expiration warning" scenario: within
                # flag_expiring_credentials' default 30-day window, but not
                # yet expired (still eligible for dispatch today).
                expires_on = today + datetime.timedelta(days=10)

            for credential_type in (
                CourierCredentialType.DRIVER_LICENSE,
                CourierCredentialType.INSURANCE,
            ):
                CourierCredential.objects.get_or_create(
                    courier=profile,
                    credential_type=credential_type,
                    defaults={
                        "status": CourierCredentialStatus.APPROVED,
                        "issued_on": datetime.date(2026, 1, 1),
                        "expires_on": expires_on,
                        "evidence_reference": f"synthetic-{credential_type}-demo.pdf",
                    },
                )

            for code in (CargoClassCode.CLASS_1, CargoClassCode.CLASS_2, CargoClassCode.CLASS_3):
                CargoAuthorization.objects.get_or_create(
                    courier=profile,
                    cargo_class=cargo_classes[code],
                    defaults={
                        "supports_refrigeration": entry["refrigerated"],
                        "is_active": True,
                    },
                )

            Vehicle.objects.get_or_create(
                courier=profile,
                plate_number=f"DEMO-{entry['username'][-3:].upper()}",
                defaults={
                    "vehicle_type": VehicleType.VAN,
                    "is_active": entry["status"] == CourierStatus.APPROVED,
                    "supports_refrigeration": entry["refrigerated"],
                },
            )
            if entry["refrigerated"]:
                Equipment.objects.get_or_create(
                    courier=profile,
                    equipment_type=EquipmentType.INSULATED_CONTAINER,
                    defaults={"is_active": True, "supports_refrigeration": True},
                )

            CourierAvailability.objects.get_or_create(
                courier=profile,
                defaults={
                    "is_online": entry["status"] == CourierStatus.APPROVED,
                    "current_service_zone": zone,
                    "max_concurrent_deliveries": 5,
                },
            )

        return result

    # -- Delivery lifecycle scenarios ---------------------------------------

    def _scenario_exists(self, key: str) -> bool:
        tag = SCENARIO_TAGS[key]
        return DeliveryRequest.objects.filter(facility_instructions__startswith=tag).exists()

    @transaction.atomic
    def _seed_ready_for_dispatch(self, organization: Organization) -> bool:
        if self._scenario_exists("ready_for_dispatch"):
            self.stdout.write("Scenario 'ready_for_dispatch' already seeded — skipping.")
            return False

        created_by = User.objects.get(username="riverside_requester_dispatcher")
        pickup = Facility.objects.get(name__contains="Riverside Urgent Care — SoHo")
        destination = Facility.objects.get(name__contains="Midtown Processing Center")
        cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_1)
        temperature_profile = TemperatureProfile.objects.get(code=TemperatureProfileCode.AMBIENT)
        now = timezone.now()

        delivery_request = create_delivery_request(
            organization=organization,
            created_by=created_by,
            service_level="scheduled",
            pickup_facility=pickup,
            destination_facility=destination,
            pickup_window_start=now + datetime.timedelta(hours=2),
            pickup_window_end=now + datetime.timedelta(hours=4),
            required_delivery_by=now + datetime.timedelta(hours=6),
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            package_count=1,
            sender_contact_name="Front Desk (Demo)",
            sender_contact_role="Clinic front desk",
            recipient_contact_name="Lab Intake (Demo)",
            recipient_contact_role="Lab intake technician",
            recipient_verification_method=RecipientVerificationMethod.NONE,
            facility_instructions=(
                f"{SCENARIO_TAGS['ready_for_dispatch']} Not yet assigned to a courier — a "
                "delivery request sitting in the open dispatch pool. Synthetic demo data."
            ),
            attest_packaging=True,
            attestation_notes="Synthetic packaging attestation (demo).",
        )
        submit_delivery_request(delivery_request, actor=created_by)
        delivery_request.refresh_from_db()
        assert (
            delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH
        ), f"Expected READY_FOR_DISPATCH, got {delivery_request.status!r}"
        return True

    @transaction.atomic
    def _seed_assigned(
        self, organization: Organization, courier: CourierProfile, actor: Any
    ) -> bool:
        if self._scenario_exists("assigned"):
            self.stdout.write("Scenario 'assigned' already seeded — skipping.")
            return False

        created_by = User.objects.get(username="northstar_requester_dispatcher")
        pickup = Facility.objects.get(name__contains="Park Slope Draw Site")
        destination = Facility.objects.get(name__contains="Midtown Processing Center")
        cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_2)
        temperature_profile = TemperatureProfile.objects.get(code=TemperatureProfileCode.AMBIENT)
        now = timezone.now()

        delivery_request = create_delivery_request(
            organization=organization,
            created_by=created_by,
            service_level="same_day",
            pickup_facility=pickup,
            destination_facility=destination,
            pickup_window_start=now + datetime.timedelta(hours=1),
            pickup_window_end=now + datetime.timedelta(hours=3),
            required_delivery_by=now + datetime.timedelta(hours=5),
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            package_count=2,
            sender_contact_name="Draw Site Coordinator (Demo)",
            sender_contact_role="Lab draw site",
            recipient_contact_name="Lab Intake (Demo)",
            recipient_contact_role="Lab intake technician",
            recipient_verification_method=RecipientVerificationMethod.NONE,
            facility_instructions=(
                f"{SCENARIO_TAGS['assigned']} Assigned to a courier but not yet advanced past "
                "assignment — demonstrates the dispatch board's assigned state. Synthetic "
                "demo data."
            ),
            attest_packaging=True,
            attestation_notes="Synthetic packaging attestation (demo).",
        )
        submit_delivery_request(delivery_request, actor=created_by)
        assign_delivery(
            delivery_request.pk,
            courier.pk,
            actor,
            reason="Demo seed: assigned for the Phase 9 walkthrough dataset.",
        )
        delivery_request.refresh_from_db()
        assert (
            delivery_request.status == DeliveryStatus.ASSIGNED
        ), f"Expected ASSIGNED, got {delivery_request.status!r}"
        return True

    @transaction.atomic
    def _seed_delivered_full_chain(
        self, organization: Organization, courier: CourierProfile, actor: Any
    ) -> bool:
        if self._scenario_exists("delivered_full_chain"):
            self.stdout.write("Scenario 'delivered_full_chain' already seeded — skipping.")
            return False

        created_by = User.objects.get(username="riverside_requester_dispatcher")
        pickup = Facility.objects.get(name__contains="Riverside Urgent Care — Chelsea")
        destination = Facility.objects.get(name__contains="Midtown Processing Center")
        cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_2)
        temperature_profile = TemperatureProfile.objects.get(
            code=TemperatureProfileCode.REFRIGERATED
        )
        now = timezone.now()

        delivery_request = create_delivery_request(
            organization=organization,
            created_by=created_by,
            service_level="stat",
            pickup_facility=pickup,
            destination_facility=destination,
            pickup_window_start=now - datetime.timedelta(hours=3),
            pickup_window_end=now - datetime.timedelta(hours=2),
            required_delivery_by=now - datetime.timedelta(hours=1),
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            package_count=1,
            approximate_weight_kg=Decimal("2.5"),
            sender_contact_name="Clinic Front Desk (Demo)",
            sender_contact_role="Clinic front desk",
            recipient_contact_name="Lab Intake (Demo)",
            recipient_contact_role="Lab intake technician",
            recipient_verification_method=RecipientVerificationMethod.PIN,
            facility_instructions=(
                f"{SCENARIO_TAGS['delivered_full_chain']} A complete, delivered lifecycle with a "
                "full custody chain (pickup proof, in-range temperature reading, recipient PIN "
                "verification, delivery proof) and a generated invoice. Synthetic demo data."
            ),
            attest_packaging=True,
            attestation_notes="Synthetic packaging attestation (demo).",
        )
        submit_delivery_request(delivery_request, actor=created_by)
        delivery_request.refresh_from_db()
        assert delivery_request.status == DeliveryStatus.READY_FOR_DISPATCH

        verification, plaintext_pin = generate_recipient_pin(
            delivery_request, recipient_name="Lab Intake (Demo)"
        )

        assign_delivery(
            delivery_request.pk,
            courier.pk,
            actor,
            reason="Demo seed: assigned for the Phase 9 full-lifecycle walkthrough.",
        )
        courier_user = courier.user
        for to_status in (
            DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
            DeliveryStatus.AT_PICKUP,
        ):
            advance_delivery_status(delivery_request.pk, courier, to_status, actor=courier_user)

        delivery_request.refresh_from_db()
        capture_proof_of_pickup(
            delivery_request,
            actor=courier_user,
            sender_name="Clinic Front Desk (Demo)",
            sender_role="Clinic front desk",
            typed_signature_name="C. Frontdesk",
        )
        for to_status in (
            DeliveryStatus.PICKED_UP,
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.AT_DESTINATION,
        ):
            advance_delivery_status(delivery_request.pk, courier, to_status, actor=courier_user)

        delivery_request.refresh_from_db()
        record_reading(
            delivery_request, temperature_c=Decimal("5.0"), actor=courier_user
        )  # in the refrigerated 2-8C range — no excursion.

        verify_recipient_pin(delivery_request, plaintext_pin, actor=courier_user)
        capture_proof_of_delivery(
            delivery_request,
            actor=courier_user,
            delivered_to_name="Lab Intake (Demo)",
            typed_signature_name="L. Intake",
        )
        transition_delivery_request(
            delivery_request,
            DeliveryStatus.DELIVERED,
            actor=courier_user,
            reason="Recipient proof captured (demo seed).",
        )
        delivery_request.refresh_from_db()
        assert (
            delivery_request.status == DeliveryStatus.DELIVERED
        ), f"Expected DELIVERED, got {delivery_request.status!r}"

        invoice = generate_invoice_for_delivery(delivery_request, created_by=actor)
        self.stdout.write(
            f"Generated invoice {invoice.invoice_number} for the delivered demo scenario."
        )
        return True

    @transaction.atomic
    def _seed_temperature_excursion(
        self, organization: Organization, courier: CourierProfile, actor: Any
    ) -> bool:
        if self._scenario_exists("temperature_excursion"):
            self.stdout.write("Scenario 'temperature_excursion' already seeded — skipping.")
            return False

        created_by = User.objects.get(username="northstar_requester_dispatcher")
        pickup = Facility.objects.get(name__contains="Midtown Processing Center")
        destination = Facility.objects.get(name__contains="Riverside Urgent Care — SoHo")
        cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_2)
        temperature_profile = TemperatureProfile.objects.get(
            code=TemperatureProfileCode.REFRIGERATED
        )
        now = timezone.now()

        delivery_request = create_delivery_request(
            organization=organization,
            created_by=created_by,
            service_level="stat",
            pickup_facility=pickup,
            destination_facility=destination,
            pickup_window_start=now - datetime.timedelta(hours=2),
            pickup_window_end=now - datetime.timedelta(hours=1),
            required_delivery_by=now + datetime.timedelta(hours=1),
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            package_count=1,
            sender_contact_name="Lab Dispatch (Demo)",
            sender_contact_role="Lab dispatch desk",
            recipient_contact_name="Urgent Care Front Desk (Demo)",
            recipient_contact_role="Clinic front desk",
            recipient_verification_method=RecipientVerificationMethod.NONE,
            facility_instructions=(
                f"{SCENARIO_TAGS['temperature_excursion']} An out-of-range temperature reading "
                "opens a real SEVERE incident and places this delivery on INCIDENT_HOLD — left "
                "open deliberately, as a live item for the incidents console demo. Synthetic "
                "demo data."
            ),
            attest_packaging=True,
            attestation_notes="Synthetic packaging attestation (demo).",
        )
        submit_delivery_request(delivery_request, actor=created_by)
        assign_delivery(
            delivery_request.pk,
            courier.pk,
            actor,
            reason="Demo seed: assigned for the Phase 9 temperature-excursion walkthrough.",
        )
        courier_user = courier.user
        for to_status in (
            DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
            DeliveryStatus.AT_PICKUP,
            DeliveryStatus.PICKED_UP,
            DeliveryStatus.IN_TRANSIT,
        ):
            advance_delivery_status(delivery_request.pk, courier, to_status, actor=courier_user)

        delivery_request.refresh_from_db()
        # 15C is well outside the seeded refrigerated range (2C-8C) — this
        # single call is what actually opens the SEVERE incident and places
        # the delivery on INCIDENT_HOLD (apps.temperature.services.record_reading).
        record_reading(delivery_request, temperature_c=Decimal("15.0"), actor=courier_user)

        delivery_request.refresh_from_db()
        assert (
            delivery_request.status == DeliveryStatus.INCIDENT_HOLD
        ), f"Expected INCIDENT_HOLD after the excursion, got {delivery_request.status!r}"
        return True

    @transaction.atomic
    def _seed_recipient_unavailable_return(
        self, organization: Organization, courier: CourierProfile, actor: Any
    ) -> bool:
        if self._scenario_exists("recipient_unavailable_return"):
            self.stdout.write("Scenario 'recipient_unavailable_return' already seeded — skipping.")
            return False

        created_by = User.objects.get(username="bkpharmacy_requester_dispatcher")
        pickup = Facility.objects.get(name__contains="Park Slope Counter")
        destination = Facility.objects.get(name__contains="Riverside Urgent Care — Williamsburg")
        cargo_class = CargoClass.objects.get(code=CargoClassCode.CLASS_1)
        temperature_profile = TemperatureProfile.objects.get(code=TemperatureProfileCode.AMBIENT)
        now = timezone.now()

        delivery_request = create_delivery_request(
            organization=organization,
            created_by=created_by,
            service_level="scheduled",
            pickup_facility=pickup,
            destination_facility=destination,
            pickup_window_start=now - datetime.timedelta(hours=4),
            pickup_window_end=now - datetime.timedelta(hours=3),
            required_delivery_by=now - datetime.timedelta(hours=1),
            cargo_class=cargo_class,
            temperature_profile=temperature_profile,
            package_count=1,
            sender_contact_name="Pharmacy Counter (Demo)",
            sender_contact_role="Pharmacy counter",
            recipient_contact_name="Clinic Front Desk (Demo)",
            recipient_contact_role="Clinic front desk",
            recipient_verification_method=RecipientVerificationMethod.NONE,
            facility_instructions=(
                f"{SCENARIO_TAGS['recipient_unavailable_return']} Recipient was unavailable at "
                "the destination; the package is returned to the sending facility via a real "
                "incident + return-resolution flow, ending RETURNED. Synthetic demo data."
            ),
            attest_packaging=True,
            attestation_notes="Synthetic packaging attestation (demo).",
        )
        submit_delivery_request(delivery_request, actor=created_by)
        assign_delivery(
            delivery_request.pk,
            courier.pk,
            actor,
            reason="Demo seed: assigned for the Phase 9 return-to-sender walkthrough.",
        )
        courier_user = courier.user
        for to_status in (
            DeliveryStatus.COURIER_EN_ROUTE_TO_PICKUP,
            DeliveryStatus.AT_PICKUP,
            DeliveryStatus.PICKED_UP,
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.AT_DESTINATION,
        ):
            advance_delivery_status(delivery_request.pk, courier, to_status, actor=courier_user)

        delivery_request.refresh_from_db()
        incident = open_incident(
            delivery_request,
            category=IncidentCategory.RECIPIENT_UNAVAILABLE,
            severity=IncidentSeverity.MODERATE,
            summary="Recipient was unavailable after two attempted hand-offs (demo seed).",
            actor=courier_user,
        )
        resolution = initiate_return(
            delivery_request,
            reason="Recipient unavailable after two attempts (demo seed).",
            actor=actor,
            incident=incident,
        )
        complete_return(resolution, actor=actor)
        resolve_incident(
            incident,
            resolution_type=IncidentResolutionType.OTHER,
            resolution_note="Package returned to the sending facility (demo seed).",
            actor=actor,
        )

        delivery_request.refresh_from_db()
        assert (
            delivery_request.status == DeliveryStatus.RETURNED
        ), f"Expected RETURNED, got {delivery_request.status!r}"
        return True
