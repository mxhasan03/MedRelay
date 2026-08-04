"""The tamper-evident chain-of-custody event log, plus pickup/delivery proof
and recipient-verification prototypes.

See docs/CURRENT_STATUS.md "Phase 6" section for the full design write-up.
Highlights:

- `CustodyEvent` is the load-bearing new mechanism this phase adds: a real,
  per-delivery SHA-256 hash chain (`apps.custody.hashing.compute_event_hash`),
  strictly stronger than Phase 2's `apps.deliveries.models.
  DeliveryStatusTransition`, which is only ORM-level append-only (no
  cryptographic tamper evidence at all — see that model's own docstring for
  its honestly-documented limits). `apps.custody.verification.
  verify_custody_chain` is the corresponding verifier.
- Event creation always goes through `apps.custody.services.record_event`
  (never `CustodyEvent.objects.create(...)` directly outside that module) —
  it is the only place that correctly computes `sequence`/`previous_hash`/
  `current_hash` under a row lock on the parent `DeliveryRequest`. The model
  itself only enforces the *append-only* half (no update/delete via the ORM,
  the same pattern `DeliveryStatusTransition` established) — it does not,
  and cannot, compute its own hash correctly in `save()`, because computing
  a correct `previous_hash` requires knowing (and locking) the delivery's
  prior event first.
- Corrections **append**, they never edit: `correction_of` is a self-FK: a
  correction is a brand new `CustodyEvent` row (event_type=
  `CORRECTION_APPENDED`) referencing the event it corrects. The original
  row is never touched — see `apps.custody.services.append_correction` and
  `apps/custody/tests/test_verification.py`'s correction test.
- `ProofOfPickup`/`ProofOfDelivery`/`RecipientVerification` are a
  deliberately lightweight **prototype**, not a legal e-signature product:
  a PIN is stored only as a salted hash (`django.contrib.auth.hashers`, the
  same PBKDF2 hasher Django uses for account passwords — no new dependency),
  never in plaintext, and a "signature" is either an HTML5 `<canvas>`
  drawing captured client-side and submitted as a base64 PNG data URL
  (`signature_data_url`, stored inline as text — a deliberate demo-scale
  simplification; seeing docs/TECH_STACK_AND_ZERO_COST_POLICY.md's
  `ObjectStorageProvider` adapter is the right home for this in a real
  deployment, not a Phase 6 concern) or a typed-name fallback
  (`typed_signature_name`) for accessibility/no-JS/automated-test
  reproducibility. Neither is a legally binding signature or a real
  biometric capture — see docs/SECURITY_COMPLIANCE_BOUNDARIES.md.

Data minimization: every field below is operational (event metadata, a
hashed PIN, a drawn/typed signature placeholder, an operational contact
name) — never a diagnosis, lab result, clinical note, medication indication,
SSN, insurance identifier, or real identity document, per
docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 2.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.custody.validators import signature_data_url_length_validator


class CustodyEventType(models.TextChoices):
    """The full required event-type vocabulary (docs/PRODUCT_REQUIREMENTS.md
    section 10). Not every value is automatically emitted by this phase's
    code yet — see docs/CURRENT_STATUS.md "Phase 6" "Known gaps" for exactly
    which are wired to a real trigger today vs. defined for a later phase.
    """

    REQUEST_CREATED = "request_created", "Request Created"
    PACKAGE_PREPARED = "package_prepared", "Package Prepared/Attested"
    COURIER_ASSIGNED = "courier_assigned", "Courier Assigned"
    COURIER_ARRIVED = "courier_arrived", "Courier Arrived"
    PICKUP_SCAN = "pickup_scan", "Pickup Scan"
    CONDITION_VERIFIED = "condition_verified", "Condition Verified"
    CUSTODY_ACCEPTED = "custody_accepted", "Custody Accepted"
    ROUTE_STARTED = "route_started", "Route Started"
    FACILITY_ARRIVAL = "facility_arrival", "Facility Arrival"
    RECIPIENT_VERIFIED = "recipient_verified", "Recipient Verified"
    DELIVERY_SCAN = "delivery_scan", "Delivery Scan"
    CUSTODY_TRANSFERRED = "custody_transferred", "Custody Transferred"
    DELIVERY_COMPLETED = "delivery_completed", "Delivery Completed"
    INCIDENT_OPENED = "incident_opened", "Incident Opened"
    INCIDENT_UPDATED = "incident_updated", "Incident Updated"
    INCIDENT_RESOLVED = "incident_resolved", "Incident Resolved"
    RETURN_INITIATED = "return_initiated", "Return Initiated"
    RETURN_COMPLETED = "return_completed", "Return Completed"
    CORRECTION_APPENDED = "correction_appended", "Correction Appended"


class CustodyActorType(models.TextChoices):
    CUSTOMER = "customer", "Customer Organization"
    COURIER = "courier", "Courier"
    INTERNAL_OPS = "internal_ops", "Internal Operations"
    RECIPIENT = "recipient", "Recipient"
    SYSTEM = "system", "System (Automated)"


class CustodyEventQuerySet(models.QuerySet["CustodyEvent"]):
    """Blocks queryset-level bulk mutation — the same convention as
    `apps.deliveries.models.DeliveryStatusTransitionQuerySet`. See the model
    docstring above and `apps/custody/tests/test_verification.py`'s tamper
    test for why this ORM-level guard is *not* the real tamper-evidence
    mechanism (it is bypassable via raw SQL, exactly like
    `DeliveryStatusTransition`'s equivalent guard) — the hash chain is.
    """

    def update(self, *args: Any, **kwargs: Any) -> int:
        raise ValidationError(
            "CustodyEvent rows are append-only; bulk queryset.update() is not allowed."
        )

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "CustodyEvent rows are append-only; bulk queryset.delete() is not allowed."
        )


class CustodyEvent(models.Model):
    """One immutable, hash-chained event in a delivery's chain of custody.

    Always create via `apps.custody.services.record_event`/`append_correction`
    — never construct and `.save()` an instance directly outside that module
    (see module docstring).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery_request = models.ForeignKey(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="custody_events"
    )
    package = models.ForeignKey(
        "cargo.Package",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custody_events",
    )
    sequence = models.PositiveIntegerField(
        editable=False, help_text="1-based position within this delivery's hash chain."
    )
    event_type = models.CharField(max_length=32, choices=CustodyEventType.choices)
    actor_type = models.CharField(max_length=16, choices=CustodyActorType.choices)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custody_events",
    )
    actor_label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human-readable actor label when no User row applies (e.g. 'system').",
    )
    occurred_at = models.DateTimeField(
        help_text="When the event actually happened (may differ from recorded_at for "
        "offline-captured or backdated events)."
    )
    recorded_at = models.DateTimeField(
        help_text="When this row was written. Deliberately not auto_now_add — set explicitly by "
        "apps.custody.services.record_event before the hash is computed, so the hash can cover "
        "it. See apps/custody/hashing.py's module docstring."
    )
    location_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_description = models.CharField(max_length=200, blank=True)
    device_metadata = models.JSONField(
        default=dict, blank=True, help_text="e.g. {'user_agent': ..., 'app_version': ...}."
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured, event-type-specific data. Never diagnosis/clinical/SSN content.",
    )
    previous_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="The prior event's current_hash (blank for the first "
        "event in a delivery's chain).",
    )
    current_hash = models.CharField(
        max_length=64,
        editable=False,
        help_text="SHA-256 hex digest over this event's canonical fields + previous_hash.",
    )
    correction_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
        help_text="Set only on CORRECTION_APPENDED events — the original event being corrected. "
        "The original row is never mutated; see module docstring.",
    )

    objects = CustodyEventQuerySet.as_manager()

    class Meta:
        ordering = ["delivery_request_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_request", "sequence"],
                name="unique_custody_event_sequence_per_delivery",
            )
        ]

    def __str__(self) -> str:
        return f"{self.delivery_request_id} #{self.sequence}: {self.get_event_type_display()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Unlike apps.deliveries.models.DeliveryStatusTransition (a plain
        # auto-incrementing BigAutoField pk, so pk is None until the first
        # INSERT), CustodyEvent.id is a UUIDField with default=uuid.uuid4 —
        # Django assigns that default at instantiation time, so `self.pk` is
        # already non-None even on a brand-new, never-saved instance.
        # `self._state.adding` (Django's own "has this instance ever been
        # saved" flag, flipped to False by the first successful save() call)
        # is the correct check here instead.
        if not self._state.adding:
            raise ValidationError(
                "CustodyEvent rows are append-only; an existing row cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("CustodyEvent rows are append-only; existing rows cannot be deleted.")


class ProofOfPickup(models.Model):
    """Sender-side hand-off proof captured by the courier at pickup.

    Deliberately signature/typed-name only (no PIN) — the sender is the
    customer organization's own on-site staff, not a remote party who needs
    identity confirmation via a shared secret; a lightweight signature
    capture is enough to record "who at the facility handed off this
    shipment." See module docstring for the signature-prototype's honest
    limitations.
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="proof_of_pickup"
    )
    sender_name = models.CharField(max_length=200, blank=True)
    sender_role = models.CharField(max_length=120, blank=True)
    signature_data_url = models.TextField(
        blank=True,
        validators=[signature_data_url_length_validator],
        help_text="Base64 data: URL PNG from the HTML5 canvas signature pad. Prototype only.",
    )
    typed_signature_name = models.CharField(
        max_length=200, blank=True, help_text="Typed-name fallback signature."
    )
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs_of_pickup_captured",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    custody_event = models.ForeignKey(
        CustodyEvent, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self) -> str:
        return f"Proof of pickup for {self.delivery_request_id}"

    @property
    def has_signature(self) -> bool:
        return bool(self.signature_data_url or self.typed_signature_name)


class RecipientVerification(models.Model):
    """The recipient-side PIN/signature verification record for one delivery.

    A PIN, when used, is generated once (`apps.custody.services.
    generate_recipient_pin`) and stored only as a salted hash
    (`django.contrib.auth.hashers.make_password` — the same hasher Django
    uses for account passwords). The plaintext PIN is returned to the caller
    exactly once, at generation time, and never persisted — see that
    function's docstring for the honest, demo-scale limitation of *how* the
    PIN reaches the recipient in this prototype (no real recipient portal or
    SMS/email delivery exists yet; Phase 7 is "notifications, recipient
    tracking, billing, and reports").
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest",
        on_delete=models.CASCADE,
        related_name="recipient_verification",
    )
    method = models.CharField(
        max_length=16,
        help_text="An apps.deliveries.models.RecipientVerificationMethod value.",
    )
    recipient_name = models.CharField(max_length=200, blank=True)
    pin_hash = models.CharField(max_length=128, blank=True)
    pin_generated_at = models.DateTimeField(null=True, blank=True)
    pin_verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipient_verifications_confirmed",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Recipient verification for {self.delivery_request_id}"

    @property
    def is_verified(self) -> bool:
        return self.pin_verified_at is not None


class ProofOfDelivery(models.Model):
    """The recipient-side hand-off proof captured by the courier at delivery.

    Gates the `AT_DESTINATION -> DELIVERED` state-machine transition — see
    `apps.deliveries.state_machine.validate_delivered`, which requires this
    row to exist (`hasattr(delivery_request, "proof_of_delivery")`) before
    `DELIVERED` is reachable.
    """

    delivery_request = models.OneToOneField(
        "deliveries.DeliveryRequest", on_delete=models.CASCADE, related_name="proof_of_delivery"
    )
    recipient_verification = models.ForeignKey(
        RecipientVerification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs_of_delivery",
    )
    delivered_to_name = models.CharField(max_length=200, blank=True)
    signature_data_url = models.TextField(
        blank=True, validators=[signature_data_url_length_validator]
    )
    typed_signature_name = models.CharField(max_length=200, blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs_of_delivery_captured",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    custody_event = models.ForeignKey(
        CustodyEvent, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self) -> str:
        return f"Proof of delivery for {self.delivery_request_id}"

    @property
    def has_signature(self) -> bool:
        return bool(self.signature_data_url or self.typed_signature_name)
