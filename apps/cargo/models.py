"""Cargo classes, policies, temperature profiles, packages, and attestations.

Design decision — `CargoClass` as a real lookup model, not a bare
`TextChoices` enum (see docs/CURRENT_STATUS.md "Phase 2" section for the
full write-up): docs/ARCHITECTURE_AND_DATA_MODEL.md section 3 lists
`CargoClass` and `CargoPolicy` as two separate entities, and `CargoPolicy`
needs somewhere to attach class-specific rules (packaging attestation
requirement, temperature eligibility). A real model with three seeded rows
(one per docs/PRODUCT_REQUIREMENTS.md section 3 class) makes that FK
relationship straightforward and gives admins/tests a stable referential
identity, at the cost of one extra lookup table for what is, in practice, a
fixed three-row taxonomy (Class 1/2/3 — frozen cargo and any other class are
explicitly out of scope; there is no UI/API path to create a fourth row).
`TemperatureProfile` gets the same treatment for the same reason (ambient/
refrigerated only, frozen explicitly deferred per docs/PRODUCT_REQUIREMENTS.md
section 3 — "Frozen cargo is deferred").

Cross-app note: `Package` and `PackagingAttestation` below hold a foreign
key to `apps.deliveries.DeliveryRequest` (via the lazy "deliveries.
DeliveryRequest" string reference, per docs/ARCHITECTURE_AND_DATA_MODEL.md's
"Cargo and packages" vs. "Delivery and dispatch" entity grouping), while
`apps.deliveries.models.DeliveryRequest` itself holds foreign keys back to
`CargoClass`/`TemperatureProfile` in this module. This is a two-way *data
model* relationship between the two apps (ordinary Django cross-app FKs,
resolved lazily by the app registry — there is no Python import cycle since
neither module imports the other's `models` module directly), not a
service-layer coupling; CLAUDE.md's "cross-app calls go through service
functions" rule is about behavior (e.g. tenant-scoping logic), not about
plain FK relations, which every app in this codebase already uses (e.g.
`Facility.organization`).

Data minimization: every field below is operational/logistics data (cargo
class code, temperature profile, dimensions, weight, a barcode-style
identifier code, a packaging/classification attestation flag) — never
diagnosis, lab result, clinical note, medication indication, SSN, or
insurance identifier, per docs/SECURITY_COMPLIANCE_BOUNDARIES.md.
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CargoClassCode(models.TextChoices):
    """The three fixed cargo classes (docs/PRODUCT_REQUIREMENTS.md section 3).

    Deliberately fixed and never user-extensible in this prototype — there
    is no create-a-new-class UI/API anywhere in the codebase. Explicitly
    excluded categories (patient transportation, Category A infectious
    substances, controlled substances, human organs, radioactive material,
    regulated medical waste, loose sharps, unsealed specimens, specialized
    blood products, emergency-response cargo, air shipments, courier
    packaging/repacking) have no corresponding class here at all — the main
    enforcement mechanism for "no prohibited cargo" is that these three
    values are the *only* ones that exist to choose from.
    """

    CLASS_1 = "class_1", "Class 1 — Documents & Non-Hazardous Supplies"
    CLASS_2 = "class_2", "Class 2 — Approved Routine Specimens"
    CLASS_3 = "class_3", "Class 3 — Sealed Non-Controlled Prescription Medication"


class CargoClass(models.Model):
    """A reference row for one of the three fixed cargo classes."""

    code = models.CharField(max_length=16, choices=CargoClassCode.choices, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "Cargo classes"

    def __str__(self) -> str:
        return self.name


class CargoPolicy(models.Model):
    """Class-specific rules attached to a `CargoClass`.

    Per docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 7 ("sender
    classification/packaging attestation required"), every seeded class
    requires a packaging attestation in this prototype — `CargoPolicy` still
    models it as a boolean rather than hard-coding it, so a future policy
    change is a data edit, not a code change.
    """

    cargo_class = models.OneToOneField(CargoClass, on_delete=models.CASCADE, related_name="policy")
    requires_packaging_attestation = models.BooleanField(default=True)
    allows_ambient = models.BooleanField(default=True)
    allows_refrigerated = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Cargo policies"

    def __str__(self) -> str:
        return f"Policy for {self.cargo_class}"

    def allows_temperature_profile(self, temperature_profile: TemperatureProfile) -> bool:
        if temperature_profile.code == TemperatureProfileCode.AMBIENT:
            return self.allows_ambient
        if temperature_profile.code == TemperatureProfileCode.REFRIGERATED:
            return self.allows_refrigerated
        return False


class TemperatureProfileCode(models.TextChoices):
    """Ambient/refrigerated only — frozen is explicitly deferred (see module docstring)."""

    AMBIENT = "ambient", "Ambient"
    REFRIGERATED = "refrigerated", "Refrigerated"


class TemperatureProfile(models.Model):
    """A reference row for one of the two supported temperature profiles.

    `min_temp_c`/`max_temp_c` (Phase 6): the allowed synthetic temperature
    range used by `apps.temperature.services.record_reading` to decide
    whether a simulated reading is an excursion. These are illustrative
    demo/reference values (e.g. a typical cold-chain 2-8C refrigerated
    range), not a medically validated cold-chain specification — see
    docs/PRODUCT_REQUIREMENTS.md section 12's "no claim of validated
    cold-chain compliance in the prototype."
    """

    code = models.CharField(max_length=16, choices=TemperatureProfileCode.choices, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    min_temp_c = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Minimum acceptable temperature (Celsius) for this profile. Blank means "
        "no lower bound is enforced (e.g. ambient).",
    )
    max_temp_c = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Maximum acceptable temperature (Celsius) for this profile. Blank means "
        "no upper bound is enforced.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name

    def in_range(self, temperature_c: Any) -> bool:
        """Whether `temperature_c` falls within this profile's [min, max] range.
        A blank bound is treated as unconstrained on that side."""
        from decimal import Decimal

        value = Decimal(str(temperature_c))
        if self.min_temp_c is not None and value < self.min_temp_c:
            return False
        return not (self.max_temp_c is not None and value > self.max_temp_c)


class Package(models.Model):
    """One physical package within a delivery request.

    Dimensions/weight are approximate, per docs/PRODUCT_REQUIREMENTS.md
    section 5's wizard field list ("approximate dimensions/weight") — these
    are logistics estimates, not precise measurements, and are never
    clinical data.
    """

    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="packages"
    )
    cargo_class = models.ForeignKey(CargoClass, on_delete=models.PROTECT, related_name="packages")
    temperature_profile = models.ForeignKey(
        TemperatureProfile, on_delete=models.PROTECT, related_name="packages"
    )
    sequence_number = models.PositiveIntegerField(
        default=1, help_text="This package's position within the delivery request's package count."
    )
    approximate_weight_kg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    approximate_length_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True
    )
    approximate_width_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True
    )
    approximate_height_cm = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text="Short operational description (e.g. 'sealed specimen bag'). Never clinical.",
    )
    scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Phase 5 (courier PWA): when the assigned courier confirmed this package's "
            "PackageIdentifier code during pickup (camera QR scan or manual code entry — see "
            "apps.cargo.services.confirm_package_scan). This is the physical act of scanning a "
            "package at pickup, not the custody/chain-of-custody proof event (recipient "
            "PIN/signature capture), which is Phase 6 work — see docs/CURRENT_STATUS.md 'Phase 5' "
            "for the exact boundary."
        ),
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scanned_packages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["delivery_request_id", "sequence_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_request", "sequence_number"],
                name="unique_package_sequence_per_delivery_request",
            )
        ]

    def __str__(self) -> str:
        return f"Package {self.sequence_number} of {self.delivery_request_id}"


def _generate_package_identifier_code() -> str:
    """A short, unique, barcode/QR-able code. Not a real barcode-symbology check digit scheme —
    a synthetic prototype identifier only."""
    return f"PKG-{uuid.uuid4().hex[:12].upper()}"


class PackageIdentifier(models.Model):
    """A barcode/QR-able identifier for one `Package`.

    QR generation uses `segno` (pure-Python, no Pillow/system dependency —
    see docs/TECH_STACK_AND_ZERO_COST_POLICY.md's "Segno or qrcode" allowed
    list). A full scanning UI is Phase 5 work; this phase only needs to
    prove the encoded value exists and can be rendered as an actual QR
    image, which `render_qr_png_bytes`/`render_qr_svg` below do.
    """

    package = models.OneToOneField(Package, on_delete=models.CASCADE, related_name="identifier")
    code = models.CharField(max_length=32, unique=True, default=_generate_package_identifier_code)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.code

    def render_qr_svg(self) -> str:
        """Render this identifier's code as an SVG QR code and return the SVG markup."""
        import segno

        qr = segno.make(self.code, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", xmldecl=False)
        return buf.getvalue().decode("utf-8")

    def render_qr_png_bytes(self) -> bytes:
        """Render this identifier's code as a PNG QR code and return the raw PNG bytes."""
        import segno

        qr = segno.make(self.code, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=4)
        return buf.getvalue()


class PackagingAttestation(models.Model):
    """The sender's attestation that packaging/classification/sealing meets policy.

    One attestation per `DeliveryRequest` (covers all packages in that
    request) — `docs/PRODUCT_REQUIREMENTS.md` section 5 lists "packaging/
    classification attestation" as a single wizard field, not a per-package
    one. `apps.deliveries.state_machine.validate_ready_for_dispatch` checks
    for this row's existence whenever `CargoPolicy.requires_packaging_
    attestation` is true for the request's cargo class.
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="packaging_attestation"
    )
    attested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="packaging_attestations",
    )
    packaging_confirmed = models.BooleanField(
        default=True,
        help_text="Sender confirms packaging/sealing/labeling meets MedRelay policy for the class.",
    )
    classification_confirmed = models.BooleanField(
        default=True,
        help_text="Sender confirms the declared cargo class accurately reflects package contents.",
    )
    notes = models.TextField(
        blank=True,
        max_length=2000,
        help_text="Optional free-text notes. Never diagnosis/clinical content.",
    )
    attested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Attestation for {self.delivery_request_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        from apps.cargo.validation import find_prohibited_cargo_keywords

        super().clean()
        hits = find_prohibited_cargo_keywords(self.notes)
        if hits:
            raise ValidationError(
                {
                    "notes": (
                        "Notes appear to reference an excluded cargo/service category "
                        f"({', '.join(hits)}). MedRelay does not support this cargo type — "
                        "see docs/PRODUCT_REQUIREMENTS.md section 3."
                    )
                }
            )


class PackageConditionCheckStage(models.TextChoices):
    PICKUP = "pickup", "At Pickup"
    DELIVERY = "delivery", "At Delivery"


class SealStatus(models.TextChoices):
    INTACT = "intact", "Intact"
    BROKEN = "broken", "Broken"
    NOT_APPLICABLE = "not_applicable", "Not Applicable"


class TemperatureIndicatorStatus(models.TextChoices):
    NOT_APPLICABLE = "not_applicable", "Not Applicable"
    OK = "ok", "Within Range"
    TRIPPED = "tripped", "Tripped/Excursion Indicated"
    UNKNOWN = "unknown", "Unknown/Unreadable"


class PackageConditionCheck(models.Model):
    """A structured pickup/delivery condition checklist for one `Package`
    (docs/ARCHITECTURE_AND_DATA_MODEL.md "Cargo and packages" entity list;
    docs/PRODUCT_REQUIREMENTS.md section 6 "condition/seal checklist").

    Linked to the `CustodyEvent` ("condition_verified") it produced — see
    `apps.cargo.services.record_condition_check`, the only intended way to
    create one (it also appends the custody event; a bare
    `PackageConditionCheck.objects.create(...)` would silently skip that).
    Placeholder/indicator status only (`TemperatureIndicatorStatus`) — no
    real IoT temperature-indicator hardware is integrated; see
    `apps.temperature` for the separate simulated-sensor-reading mechanism.
    """

    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="condition_checks")
    stage = models.CharField(max_length=16, choices=PackageConditionCheckStage.choices)
    seal_status = models.CharField(
        max_length=16, choices=SealStatus.choices, default=SealStatus.NOT_APPLICABLE
    )
    physical_damage_observed = models.BooleanField(default=False)
    damage_description = models.CharField(
        max_length=300, blank=True, help_text="Short operational description. Never clinical."
    )
    temperature_indicator_status = models.CharField(
        max_length=16,
        choices=TemperatureIndicatorStatus.choices,
        default=TemperatureIndicatorStatus.NOT_APPLICABLE,
    )
    notes = models.TextField(blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="package_condition_checks",
    )
    checked_at = models.DateTimeField(auto_now_add=True)
    custody_event = models.ForeignKey(
        "custody.CustodyEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="condition_checks",
    )

    class Meta:
        ordering = ["package_id", "stage"]
        constraints = [
            models.UniqueConstraint(
                fields=["package", "stage"], name="unique_condition_check_per_package_stage"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_stage_display()} condition check — {self.package_id}"

    @property
    def has_any_concern(self) -> bool:
        return (
            self.seal_status == SealStatus.BROKEN
            or self.physical_damage_observed
            or self.temperature_indicator_status == TemperatureIndicatorStatus.TRIPPED
        )
