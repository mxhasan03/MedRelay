"""Courier onboarding profiles, credentials, training, vehicles, equipment,
cargo authorizations, and availability.

Design decisions (see docs/CURRENT_STATUS.md "Phase 3" section for the full
write-up):

1. **Couriers are `User` rows, distinguished the same way Phase 1
   distinguished internal staff.** `apps.accounts.models.User.is_courier` is
   a cheap boolean flag (mirroring `is_internal_staff`), kept in sync by
   `CourierProfile.save()` below. There is no third parallel "membership"
   table — `CourierProfile` (this module) holds the actual onboarding data,
   exactly as `InternalRoleAssignment` holds the actual internal-role data.
   Like `is_internal_staff`, `is_courier` alone grants no access.
2. **`CourierStatus` is a plain field on `CourierProfile`, not a separate
   append-only history model.** Phase 2's `DeliveryStatusTransition` is a
   real append-only audit log because delivery-status history is
   operationally load-bearing (proof of when a delivery moved through the
   pipeline, used for SLA/incident analysis). A courier's coarse
   applicant/approved/suspended/inactive status changes far less often and
   has no equivalent Phase 3 consumer that needs a full history — a
   dedicated audit trail for *every* status/attribute change across the
   whole system is explicitly deferred to Phase 8's "audit viewer" work
   (docs/IMPLEMENTATION_ROADMAP.md), which is the right place to build one
   general mechanism rather than a bespoke one here. `updated_at` on
   `CourierProfile` at least records *when* the row last changed, even
   without a full log of *what* changed.
3. **`CourierCredential.evidence_reference` is a placeholder text
   reference, never a real uploaded document.** Per docs/PRODUCT_REQUIREMENTS.md
   section 6 ("No real background-check provider is integrated in the
   zero-cost prototype") and docs/ARCHITECTURE_AND_DATA_MODEL.md's "never
   store real sensitive documents in the demo repository": this field is a
   short free-text label/synthetic filename string (e.g.
   "synthetic-drivers-license-demo.pdf"), not a `FileField`/`ImageField`. No
   file upload path exists anywhere in this app.
4. **`CourierAvailability.max_concurrent_deliveries` is a configured
   *capacity limit*, not a live workload counter.** There is no
   `DeliveryAssignment` model yet (that is Phase 4), so there is nothing to
   count concurrent active deliveries against — Phase 3's honest current
   workload is always 0 (see `apps.couriers.eligibility` for where this
   proxy is used and documented again at the point of use).
5. **`CourierLocationPing` and `CourierPerformanceSnapshot` are
   intentionally not built in this phase** — they belong to Phase 5
   (tracking) and Phase 4 (dispatch scoring history) respectively, per
   docs/ARCHITECTURE_AND_DATA_MODEL.md's "Couriers" entity list.

Data minimization: every field below is operational/eligibility status data
(a status enum, a credential type/expiry date, a vehicle type/plate
placeholder, a service-zone reference) — never a real driver's license
number, real SSN, real insurance policy number, or real uploaded ID
document, per docs/SECURITY_COMPLIANCE_BOUNDARIES.md. This is the first
phase handling anything identity-document-*adjacent*, so this call-out is
made explicitly here and again on the fields most likely to be misused.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone


class CourierStatus(models.TextChoices):
    """Courier roles (docs/PRODUCT_REQUIREMENTS.md section 4 "Courier roles")."""

    APPLICANT = "applicant", "Applicant"
    APPROVED = "approved", "Approved Courier"
    SUSPENDED = "suspended", "Suspended Courier"
    INACTIVE = "inactive", "Inactive Courier"


class IdentityReviewStatus(models.TextChoices):
    """Placeholder manual identity-review status (docs/PRODUCT_REQUIREMENTS.md
    section 6 "identity-review status placeholder"). No real background-check
    provider is integrated — this is set by a human
    `courier_onboarding_reviewer` after reviewing (synthetic, in this demo)
    evidence, never computed automatically.
    """

    NOT_STARTED = "not_started", "Not Started"
    PENDING_REVIEW = "pending_review", "Pending Review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DriverLicenseStatus(models.TextChoices):
    """Placeholder manual driver-license status (docs/PRODUCT_REQUIREMENTS.md
    section 6 "driver-license status")."""

    NOT_SUBMITTED = "not_submitted", "Not Submitted"
    PENDING_REVIEW = "pending_review", "Pending Review"
    VALID = "valid", "Valid"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"


class InsuranceStatus(models.TextChoices):
    """Placeholder manual insurance status (docs/PRODUCT_REQUIREMENTS.md
    section 6 "insurance status")."""

    NOT_SUBMITTED = "not_submitted", "Not Submitted"
    PENDING_REVIEW = "pending_review", "Pending Review"
    VALID = "valid", "Valid"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"


class CourierProfile(models.Model):
    """One onboarding/eligibility profile per courier `User`.

    `identity_review_status`/`driver_license_status`/`insurance_status` are
    coarse, manually-set onboarding-progress indicators (see module
    docstring point 3 and `IdentityReviewStatus`/`DriverLicenseStatus`/
    `InsuranceStatus` above) — the actual per-credential expiry-date
    bookkeeping used by the eligibility engine's "credential expired" hard
    filter lives in `CourierCredential` below, not here.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="courier_profile",
    )
    status = models.CharField(
        max_length=16, choices=CourierStatus.choices, default=CourierStatus.APPLICANT
    )
    identity_review_status = models.CharField(
        max_length=16,
        choices=IdentityReviewStatus.choices,
        default=IdentityReviewStatus.NOT_STARTED,
    )
    driver_license_status = models.CharField(
        max_length=16,
        choices=DriverLicenseStatus.choices,
        default=DriverLicenseStatus.NOT_SUBMITTED,
    )
    insurance_status = models.CharField(
        max_length=16,
        choices=InsuranceStatus.choices,
        default=InsuranceStatus.NOT_SUBMITTED,
    )
    home_service_zone = models.ForeignKey(
        "facilities.ServiceZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_couriers",
        help_text="The courier's default/home service zone, used as an eligibility fallback "
        "when no current CourierAvailability.current_service_zone is set.",
    )
    phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(
        blank=True,
        help_text="Internal operational notes only. Never a diagnosis/clinical/SSN/ID-number.",
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self) -> str:
        return f"{self.user} ({self.get_status_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Keep User.is_courier in sync, exactly the way
        # InternalRoleAssignment.save() keeps User.is_internal_staff in
        # sync (see apps/accounts/models.py module docstring point added
        # in Phase 3). is_courier itself grants no access.
        super().save(*args, **kwargs)
        if not self.user.is_courier:
            self.user.is_courier = True
            self.user.save(update_fields=["is_courier"])


class CourierCredentialType(models.TextChoices):
    """Per docs/ARCHITECTURE_AND_DATA_MODEL.md's "Couriers" entity group.

    `DRIVER_LICENSE` and `INSURANCE` are the two credential types the
    eligibility engine's "credential expired" hard filter requires to be
    present and unexpired (see `apps.couriers.eligibility.
    REQUIRED_CREDENTIAL_TYPES`); the rest exist for onboarding-record
    completeness but are not (yet) load-bearing for eligibility.
    """

    DRIVER_LICENSE = "driver_license", "Driver License"
    INSURANCE = "insurance", "Insurance"
    IDENTITY_VERIFICATION = "identity_verification", "Identity Verification"
    BACKGROUND_CHECK_PLACEHOLDER = (
        "background_check_placeholder",
        "Background Check (Placeholder — No Real Provider Integrated)",
    )
    OTHER = "other", "Other"


class CourierCredentialStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending Review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class CourierCredentialQuerySet(models.QuerySet["CourierCredential"]):
    def expiring_within(self, days: int, *, as_of: datetime.date | None = None) -> Any:
        """Credentials that are currently `APPROVED` and expire within `days` days
        (inclusive) of `as_of` (defaults to today).

        This is query/flagging logic only — per docs/PRODUCT_REQUIREMENTS.md
        section 6, real notifications about expiring credentials are Phase 7
        work (`apps.notifications`); nothing here sends an email/SMS/in-app
        notification.
        """
        reference_date = as_of or timezone.localdate()
        horizon = reference_date + datetime.timedelta(days=days)
        return self.filter(
            status=CourierCredentialStatus.APPROVED,
            expires_on__isnull=False,
            expires_on__gte=reference_date,
            expires_on__lte=horizon,
        )

    def expired(self, *, as_of: datetime.date | None = None) -> Any:
        reference_date = as_of or timezone.localdate()
        return self.filter(expires_on__isnull=False, expires_on__lt=reference_date)


class CourierCredential(models.Model):
    """One credential record (type, status, validity window, reviewer, evidence
    placeholder) for a courier.

    `evidence_reference` is a placeholder text reference only — see module
    docstring point 3. Never a real uploaded document; never a real
    driver's-license/policy number.
    """

    courier = models.ForeignKey(
        CourierProfile, on_delete=models.CASCADE, related_name="credentials"
    )
    credential_type = models.CharField(max_length=32, choices=CourierCredentialType.choices)
    status = models.CharField(
        max_length=16,
        choices=CourierCredentialStatus.choices,
        default=CourierCredentialStatus.PENDING_REVIEW,
    )
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_courier_credentials",
        help_text="Typically a courier_onboarding_reviewer or compliance_reviewer internal user.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    evidence_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Placeholder reference only (e.g. a synthetic filename/label such as "
            "'synthetic-drivers-license-demo.pdf') — never a real uploaded document or a real "
            "identity-document number. This prototype never stores real sensitive documents."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CourierCredentialQuerySet.as_manager()

    class Meta:
        ordering = ["courier__user__username", "credential_type"]

    def __str__(self) -> str:
        credential_type = self.get_credential_type_display()
        return f"{self.courier} — {credential_type} ({self.get_status_display()})"

    @property
    def is_expired(self) -> bool:
        return self.expires_on is not None and self.expires_on < timezone.localdate()


class TrainingRecordType(models.TextChoices):
    GENERAL_ORIENTATION = "general_orientation", "General Orientation"
    CARGO_HANDLING = "cargo_handling", "Cargo Handling"
    COLD_CHAIN_HANDLING = "cold_chain_handling", "Cold Chain Handling"
    SAFETY = "safety", "Safety"
    OTHER = "other", "Other"


class TrainingRecord(models.Model):
    """A completed (or expired) training record for a courier.

    Not currently a hard-eligibility filter (docs/PRODUCT_REQUIREMENTS.md
    section 11's hard-filter list has no "training missing" entry, and none
    of Phase 2's three seeded cargo classes are modeled as requiring a
    specific training certification in this prototype) — kept as an
    onboarding-record model per docs/PRODUCT_REQUIREMENTS.md section 6
    ("training records"), available for a later phase to wire into
    eligibility if a cargo class ever needs it.
    """

    courier = models.ForeignKey(
        CourierProfile, on_delete=models.CASCADE, related_name="training_records"
    )
    training_type = models.CharField(max_length=32, choices=TrainingRecordType.choices)
    completed_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["courier__user__username", "training_type"]

    def __str__(self) -> str:
        return f"{self.courier} — {self.get_training_type_display()}"


class VehicleType(models.TextChoices):
    SEDAN = "sedan", "Sedan"
    VAN = "van", "Van"
    MOTORCYCLE = "motorcycle", "Motorcycle"
    BICYCLE = "bicycle", "Bicycle"
    ON_FOOT = "on_foot", "On Foot"
    OTHER = "other", "Other"


class Vehicle(models.Model):
    """A vehicle a courier may use. A courier may have more than one (e.g. a
    bicycle for ambient documents and a refrigerated van for Class 2/3 work).
    """

    courier = models.ForeignKey(CourierProfile, on_delete=models.CASCADE, related_name="vehicles")
    vehicle_type = models.CharField(max_length=16, choices=VehicleType.choices)
    make = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    plate_number = models.CharField(
        max_length=32,
        blank=True,
        help_text="Synthetic placeholder plate/registration string only — never a real plate.",
    )
    supports_refrigeration = models.BooleanField(
        default=False,
        help_text="True if this vehicle itself provides refrigerated capability "
        "(e.g. a built-in cold box), used by the 'vehicle/equipment incompatible' "
        "hard-eligibility filter.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["courier__user__username"]

    def __str__(self) -> str:
        return f"{self.get_vehicle_type_display()} — {self.courier}"


class EquipmentType(models.TextChoices):
    INSULATED_CONTAINER = "insulated_container", "Insulated Container"
    REFRIGERATED_CONTAINER = "refrigerated_container", "Refrigerated Container"
    COOLER_WITH_ICE_PACKS = "cooler_with_ice_packs", "Cooler with Ice Packs"
    LOCKBOX = "lockbox", "Lockbox"
    PPE_KIT = "ppe_kit", "PPE Kit"
    OTHER = "other", "Other"


class Equipment(models.Model):
    """A piece of equipment a courier carries (separate from their vehicle),
    e.g. an insulated/refrigerated container used for Class 2/3 refrigerated
    packages.
    """

    courier = models.ForeignKey(CourierProfile, on_delete=models.CASCADE, related_name="equipment")
    equipment_type = models.CharField(max_length=32, choices=EquipmentType.choices)
    supports_refrigeration = models.BooleanField(
        default=False,
        help_text="True if this equipment provides refrigerated capability, used by the "
        "'vehicle/equipment incompatible' hard-eligibility filter.",
    )
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["courier__user__username", "equipment_type"]
        verbose_name_plural = "Equipment"

    def __str__(self) -> str:
        return f"{self.get_equipment_type_display()} — {self.courier}"


class CargoAuthorization(models.Model):
    """Links a courier to which `CargoClass` (and, via `supports_refrigeration`,
    which temperature capability) they are authorized for
    (docs/ARCHITECTURE_AND_DATA_MODEL.md "Couriers" entity list).

    One row per (courier, cargo_class): `supports_refrigeration` on that row
    records whether the courier is *also* authorized to handle that cargo
    class under refrigerated conditions, so a single row answers both the
    "cargo authorization missing" and "temperature capability missing"
    hard-eligibility filters for that cargo class without a separate
    many-to-many join table.
    """

    courier = models.ForeignKey(
        CourierProfile, on_delete=models.CASCADE, related_name="cargo_authorizations"
    )
    cargo_class = models.ForeignKey(
        "cargo.CargoClass", on_delete=models.PROTECT, related_name="courier_authorizations"
    )
    supports_refrigeration = models.BooleanField(
        default=False,
        help_text="True if the courier is authorized to handle this cargo class under "
        "refrigerated conditions, not just ambient.",
    )
    is_active = models.BooleanField(default=True)
    authorized_at = models.DateTimeField(auto_now_add=True)
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_cargo_authorizations",
    )

    class Meta:
        ordering = ["courier__user__username", "cargo_class__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["courier", "cargo_class"],
                name="unique_cargo_authorization_per_courier_per_class",
            )
        ]

    def __str__(self) -> str:
        return f"{self.courier} — {self.cargo_class}"


class CourierAvailability(models.Model):
    """A courier's current online/offline state, shift window, current service
    zone, and configured capacity (docs/PRODUCT_REQUIREMENTS.md section 6
    "Availability").

    One row per courier (`OneToOneField`) — Phase 3 tracks current state
    only, not a history of availability changes (see module docstring point
    2 for the same "history vs. plain field" reasoning applied to
    `CourierStatus`).

    `max_concurrent_deliveries` is a *configured capacity limit*, not a live
    workload counter — see module docstring point 4 and
    `apps.couriers.eligibility` for the honest "always 0" workload proxy
    this is checked against in Phase 3.
    """

    courier = models.OneToOneField(
        CourierProfile, on_delete=models.CASCADE, related_name="availability"
    )
    is_online = models.BooleanField(default=False)
    shift_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Local shift start time (facility-local, America/New_York). "
        "Blank means no shift restriction while online.",
    )
    shift_end = models.TimeField(null=True, blank=True)
    current_service_zone = models.ForeignKey(
        "facilities.ServiceZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="couriers_currently_here",
    )
    max_concurrent_deliveries = models.PositiveIntegerField(
        default=1,
        help_text="Configured capacity limit. Phase 3 has no DeliveryAssignment model yet, so "
        "current workload is always treated as 0 — see apps.couriers.eligibility.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Courier availability"
        ordering = ["courier__user__username"]

    def __str__(self) -> str:
        state = "online" if self.is_online else "offline"
        return f"{self.courier} ({state})"


class CourierActionIdempotencyKey(models.Model):
    """Phase 5's idempotency mechanism (docs/ARCHITECTURE_AND_DATA_MODEL.md
    section 9: "require Idempotency-Key for create/transition endpoints"),
    covering every new courier-facing state-mutating endpoint: job offer
    accept/decline, pickup/transit status transitions, and location pings.

    The courier's PWA generates a fresh client-side key (a UUID) once per
    logical action (see `static/js/offline-queue.js`) and sends it as the
    `Idempotency-Key` request header (or an `idempotency_key` form field as a
    no-JS fallback). `apps.couriers.idempotency.idempotent_call` is the single
    call site every affected view goes through: it looks up an existing row
    for `(courier, endpoint, key)` first and, if found, replays its stored
    `response_data` instead of re-running the underlying service call — this
    is what makes retries from the offline event queue (submitted after
    connectivity returns, possibly more than once) safe: the *effect*
    (a `DeliveryAssignment`, a `DeliveryStatusTransition`, a
    `CourierLocationPing`) is created at most once per key, no matter how many
    times the same request is replayed.

    Scoped per-courier (not globally unique on `key` alone) so two different
    couriers' independently-generated UUIDs can never collide with each
    other — a courier only ever needs uniqueness against their own prior
    requests. `endpoint` further scopes the key so the same client-generated
    UUID reused (by a client bug) across two conceptually different actions
    does not falsely dedupe them against each other.

    Only successful outcomes are recorded here (see `apps.couriers.idempotency`
    for the exact behavior on failure) — a request that legitimately fails
    (e.g. an invalid transition) is never remembered, so retrying it with a
    corrected request under the *same* key is still possible.
    """

    courier = models.ForeignKey(
        CourierProfile, on_delete=models.CASCADE, related_name="action_idempotency_keys"
    )
    endpoint = models.CharField(
        max_length=64,
        help_text="Logical endpoint name, e.g. 'job_offer_accept', 'delivery_status_advance', "
        "'location_ping'.",
    )
    key = models.CharField(max_length=255, help_text="Client-generated Idempotency-Key value.")
    response_data = models.JSONField(
        default=dict, blank=True, help_text="The stored successful response, replayed on retry."
    )
    status_code = models.PositiveSmallIntegerField(default=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["courier", "endpoint", "key"],
                name="unique_courier_action_idempotency_key",
            )
        ]
        verbose_name = "Courier action idempotency key"
        verbose_name_plural = "Courier action idempotency keys"

    def __str__(self) -> str:
        return f"{self.courier} — {self.endpoint} [{self.key}]"
