"""Delivery requests, stops, the delivery state machine, pricing, and recurring routes.

See docs/CURRENT_STATUS.md "Phase 2" section for the full set of design
write-ups this module implements against
(docs/PRODUCT_REQUIREMENTS.md sections 2, 3, 5, 9, 14;
docs/ARCHITECTURE_AND_DATA_MODEL.md sections 3, 5, 9). Highlights:

- `DeliveryRequest.id` is a UUID primary key (per
  docs/ARCHITECTURE_AND_DATA_MODEL.md section 4), unlike the auto
  `BigAutoField` ids used elsewhere in the codebase so far — delivery IDs
  are the one identifier explicitly meant to appear in operational contexts
  (tracking links, barcodes) where a guessable sequential integer would be
  an information-disclosure smell.
- `cargo_class`/`temperature_profile` are nullable at the model level so a
  `DRAFT` row can exist before the wizard's required fields are filled in;
  `apps.deliveries.state_machine.validate_ready_for_dispatch` is what
  actually enforces "required before READY_FOR_DISPATCH", not a DB
  NOT NULL constraint (see that module's docstring for why).
- `version` is a plain integer bumped by
  `apps.deliveries.services.update_delivery_request_with_version_check` for
  optimistic concurrency (docs/ARCHITECTURE_AND_DATA_MODEL.md section 9) —
  Phase 2 does not yet have concurrent writers (that starts with dispatch/
  assignment in Phase 4), but the field and the check function exist now so
  later phases don't need a schema migration for it.
- Sender/recipient "contact" fields are operational contacts at a facility
  (name/role/phone) — never patient identifiers, per
  docs/SECURITY_COMPLIANCE_BOUNDARIES.md section 2.
- `DeliveryStatusTransition` is append-only at the Django ORM layer (both
  instance `save()`/`delete()` and queryset-level `update()`/`delete()` are
  overridden to raise) — see that model's docstring for the honest strength/
  weakness of this guard.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ServiceLevel(models.TextChoices):
    """docs/PRODUCT_REQUIREMENTS.md section 2 "Delivery modes"."""

    SCHEDULED = "scheduled", "Scheduled"
    SAME_DAY = "same_day", "Same Day"
    STAT = "stat", "STAT"


class DeliveryStatus(models.TextChoices):
    """The full delivery state machine (docs/PRODUCT_REQUIREMENTS.md section 9).

    Every state is defined now so later phases never need to migrate this
    enum. Phase 2 only implements/enforces transitions among `DRAFT`,
    `SUBMITTED`, `VALIDATION_REQUIRED`, `READY_FOR_DISPATCH`, and
    `CANCELLED` — see `apps.deliveries.state_machine.ALLOWED_TRANSITIONS`
    for the authoritative, load-bearing subset. `OFFERED` onward through
    `DELIVERED`, and the exception states `REJECTED`/`INCIDENT_HOLD`/
    `RETURNING`/`RETURNED`/`FAILED`, are placeholder values only in Phase 2:
    they exist in this choices list (and can be stored on a row) but no
    service function in this phase transitions a delivery into or out of
    them. That is Phase 4 (dispatch)/Phase 5 (courier PWA)/Phase 6
    (custody/incidents) work.
    """

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    VALIDATION_REQUIRED = "validation_required", "Validation Required"
    READY_FOR_DISPATCH = "ready_for_dispatch", "Ready for Dispatch"
    OFFERED = "offered", "Offered"
    ASSIGNED = "assigned", "Assigned"
    COURIER_EN_ROUTE_TO_PICKUP = "courier_en_route_to_pickup", "Courier En Route to Pickup"
    AT_PICKUP = "at_pickup", "At Pickup"
    PICKED_UP = "picked_up", "Picked Up"
    IN_TRANSIT = "in_transit", "In Transit"
    AT_DESTINATION = "at_destination", "At Destination"
    DELIVERED = "delivered", "Delivered"
    # Exception / terminal states.
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"
    INCIDENT_HOLD = "incident_hold", "Incident Hold"
    RETURNING = "returning", "Returning"
    RETURNED = "returned", "Returned"
    FAILED = "failed", "Failed"


class RecipientVerificationMethod(models.TextChoices):
    """docs/PRODUCT_REQUIREMENTS.md section 5 wizard field "recipient verification method".

    Phase 2 only stores the chosen method as a plain field; the actual
    PIN/signature capture flow is Phase 6 (custody/proof) per
    docs/IMPLEMENTATION_ROADMAP.md.
    """

    NONE = "none", "None"
    PIN = "pin", "PIN Code"
    SIGNATURE = "signature", "Signature"


class DeliveryRequestQuerySet(models.QuerySet["DeliveryRequest"]):
    def for_user(self, user: Any) -> Any:
        from apps.organizations.services import scope_queryset_to_user_orgs

        return scope_queryset_to_user_orgs(self, user, org_field="organization_id")


class DeliveryRequest(models.Model):
    """A customer's request to move cargo from a pickup facility to a destination.

    Pickup/destination facilities are not direct FK fields here — they are
    `DeliveryStop` rows (see below), matching
    docs/ARCHITECTURE_AND_DATA_MODEL.md's separate `DeliveryStop` entity and
    leaving room for genuinely multi-stop routes in a later phase without a
    schema change to this model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="delivery_requests"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_delivery_requests",
    )

    service_level = models.CharField(max_length=16, choices=ServiceLevel.choices)
    status = models.CharField(
        max_length=32, choices=DeliveryStatus.choices, default=DeliveryStatus.DRAFT
    )

    pickup_window_start = models.DateTimeField()
    pickup_window_end = models.DateTimeField()
    required_delivery_by = models.DateTimeField()

    cargo_class = models.ForeignKey(
        "cargo.CargoClass",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delivery_requests",
    )
    temperature_profile = models.ForeignKey(
        "cargo.TemperatureProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delivery_requests",
    )
    package_count = models.PositiveIntegerField(default=1)
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

    # Operational contacts only — never patient identifiers (see module docstring).
    sender_contact_name = models.CharField(max_length=200, blank=True)
    sender_contact_phone = models.CharField(max_length=32, blank=True)
    sender_contact_role = models.CharField(max_length=120, blank=True)
    recipient_contact_name = models.CharField(max_length=200, blank=True)
    recipient_contact_phone = models.CharField(max_length=32, blank=True)
    recipient_contact_role = models.CharField(max_length=120, blank=True)

    recipient_verification_method = models.CharField(
        max_length=16,
        choices=RecipientVerificationMethod.choices,
        default=RecipientVerificationMethod.NONE,
    )
    facility_instructions = models.TextField(
        blank=True, help_text="Operational handling notes for couriers/facilities."
    )

    estimated_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    final_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Not set in Phase 2 — invoicing/billing is Phase 7.",
    )

    version = models.PositiveIntegerField(
        default=1, help_text="Optimistic-concurrency version counter."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DeliveryRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Delivery {self.id} ({self.get_status_display()})"

    def clean(self) -> None:
        from apps.cargo.validation import find_prohibited_cargo_keywords

        super().clean()
        hits = find_prohibited_cargo_keywords(self.facility_instructions)
        if hits:
            raise ValidationError(
                {
                    "facility_instructions": (
                        "Instructions appear to reference an excluded cargo/service category "
                        f"({', '.join(hits)}). MedRelay does not support this cargo type — "
                        "see docs/PRODUCT_REQUIREMENTS.md section 3."
                    )
                }
            )

    @property
    def pickup_stop(self) -> DeliveryStop | None:
        return next((s for s in self.stops.all() if s.stop_type == StopType.PICKUP), None)

    @property
    def destination_stop(self) -> DeliveryStop | None:
        return next((s for s in self.stops.all() if s.stop_type == StopType.DESTINATION), None)

    @property
    def has_packaging_attestation(self) -> bool:
        return hasattr(self, "packaging_attestation")


class StopType(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    DESTINATION = "destination", "Destination"


class DeliveryStop(models.Model):
    """A pickup or destination facility reference for a delivery request.

    Modeled as its own table (rather than direct FKs on `DeliveryRequest`)
    so a later phase can add genuinely multi-stop recurring routes without
    a schema change — Phase 2 always creates exactly one `PICKUP` and one
    `DESTINATION` stop per delivery request (see
    `apps.deliveries.services.create_delivery_request`).
    """

    delivery_request = models.ForeignKey(
        DeliveryRequest, on_delete=models.CASCADE, related_name="stops"
    )
    stop_type = models.CharField(max_length=16, choices=StopType.choices)
    sequence = models.PositiveIntegerField(default=1)
    facility = models.ForeignKey(
        "facilities.Facility", on_delete=models.PROTECT, related_name="delivery_stops"
    )
    scheduled_window_start = models.DateTimeField(null=True, blank=True)
    scheduled_window_end = models.DateTimeField(null=True, blank=True)
    instructions = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["delivery_request_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["delivery_request", "stop_type"],
                name="unique_stop_type_per_delivery_request",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_stop_type_display()} @ {self.facility} ({self.delivery_request_id})"


class DeliveryStatusTransitionQuerySet(models.QuerySet["DeliveryStatusTransition"]):
    """Blocks queryset-level bulk mutation, complementing the instance-level guard below."""

    def update(self, *args: Any, **kwargs: Any) -> int:
        raise ValidationError(
            "DeliveryStatusTransition rows are append-only; bulk queryset.update() is not allowed."
        )

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "DeliveryStatusTransition rows are append-only; bulk queryset.delete() is not allowed."
        )


class DeliveryStatusTransition(models.Model):
    """An append-only log row for one delivery status change.

    This is a lightweight precursor to the full tamper-evident custody-event
    hash chain that arrives in Phase 6 (docs/IMPLEMENTATION_ROADMAP.md) —
    deliberately *not* building hash chaining here.

    Append-only enforcement, and its honest limits: `save()` refuses to
    write when `self.pk` is already set (i.e. this is an update to an
    existing row, not a first insert), `delete()` always refuses, and the
    custom queryset above refuses bulk `update()`/`delete()` too. This
    covers every mutation path that goes through the Django ORM. It is
    **not** a database-level trigger or permission grant — a raw SQL
    statement issued outside the ORM (e.g. `UPDATE ... SET`) or a direct
    database client could still bypass it. A real DB-level guard (Postgres
    rule/trigger, or a REVOKE UPDATE/DELETE grant) is a reasonable Phase 6
    addition alongside the hash-chain verifier; Phase 2 only claims
    ORM-level enforcement, not a hard database guarantee.
    """

    delivery_request = models.ForeignKey(
        DeliveryRequest, on_delete=models.CASCADE, related_name="status_transitions"
    )
    from_status = models.CharField(max_length=32, choices=DeliveryStatus.choices, blank=True)
    to_status = models.CharField(max_length=32, choices=DeliveryStatus.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_status_transitions",
    )
    reason = models.TextField(blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    objects = DeliveryStatusTransitionQuerySet.as_manager()

    class Meta:
        ordering = ["occurred_at", "id"]

    def __str__(self) -> str:
        return f"{self.delivery_request_id}: {self.from_status or '(none)'} -> {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ValidationError(
                "DeliveryStatusTransition rows are append-only; an existing row cannot be updated."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            "DeliveryStatusTransition rows are append-only; existing rows cannot be deleted."
        )


class PricingRuleKey(models.TextChoices):
    """Named, configurable constants for the synthetic quote engine
    (docs/PRODUCT_REQUIREMENTS.md section 14). Real values live in seeded
    `PricingRule` rows (a data migration), not in code, so a demo operator
    can tune them from the admin without a deploy.
    """

    BASE_FEE = "base_fee", "Base fee"
    PER_KM_RATE = "per_km_rate", "Per-kilometer distance rate"
    PER_MINUTE_RATE = "per_minute_rate", "Per-minute time rate"
    AVERAGE_SPEED_KMH = "average_speed_kmh", "Assumed average travel speed (km/h)"
    SAME_DAY_SURCHARGE = "same_day_surcharge", "Same-day service-level surcharge"
    STAT_SURCHARGE = "stat_surcharge", "STAT service-level surcharge"
    CARGO_CLASS_2_SURCHARGE = "cargo_class_2_surcharge", "Class 2 cargo/equipment surcharge"
    CARGO_CLASS_3_SURCHARGE = "cargo_class_3_surcharge", "Class 3 cargo/equipment surcharge"
    REFRIGERATED_SURCHARGE = "refrigerated_surcharge", "Refrigerated equipment surcharge"
    AFTER_HOURS_SURCHARGE = "after_hours_surcharge", "After-hours surcharge"
    INTER_BOROUGH_TOLL_ESTIMATE = "inter_borough_toll_estimate", "Inter-borough toll estimate"
    WAIT_TIME_PLACEHOLDER_FEE = "wait_time_placeholder_fee", "Wait-time placeholder fee"
    RETURN_TRIP_FEE = "return_trip_fee", "Return-trip fee"


class PricingRule(models.Model):
    """One named, admin-editable synthetic pricing constant.

    All monetary values here are synthetic/configurable and used only to
    compute a demo quote — see docs/CURRENT_STATUS.md "Phase 2" section
    ("Quote engine" design decision) and docs/PRODUCT_REQUIREMENTS.md
    section 14 ("Use synthetic configurable rules only. Do not connect a
    real payment processor.").
    """

    key = models.CharField(max_length=32, choices=PricingRuleKey.choices, unique=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return f"{self.get_key_display()} = {self.amount}"


class Quote(models.Model):
    """A persisted, itemized synthetic price quote for one delivery request.

    Recomputed (overwritten) each time `apps.deliveries.pricing.quote_delivery_request`
    runs for a given request — Phase 2 keeps one current quote per request,
    not a quote history table.
    """

    delivery_request = models.OneToOneField(
        DeliveryRequest, on_delete=models.CASCADE, related_name="quote"
    )
    base_fee = models.DecimalField(max_digits=8, decimal_places=2)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2)
    distance_time_fee = models.DecimalField(max_digits=8, decimal_places=2)
    service_level_surcharge = models.DecimalField(max_digits=8, decimal_places=2)
    cargo_equipment_surcharge = models.DecimalField(max_digits=8, decimal_places=2)
    toll_estimate = models.DecimalField(max_digits=8, decimal_places=2)
    wait_time_fee = models.DecimalField(max_digits=8, decimal_places=2)
    after_hours_surcharge = models.DecimalField(max_digits=8, decimal_places=2)
    return_trip_fee = models.DecimalField(max_digits=8, decimal_places=2)
    total_price = models.DecimalField(max_digits=8, decimal_places=2)
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Quote for {self.delivery_request_id}: {self.total_price}"


class RecurrenceFrequency(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"


class RecurringRoute(models.Model):
    """A recurring delivery route (docs/PRODUCT_REQUIREMENTS.md section 5 "Recurring routes").

    Phase 2 scope is data model + basic admin/service CRUD only. The actual
    job that generates concrete `DeliveryRequest` rows on schedule from a
    `RecurringRoute` is **not implemented** in this phase — see
    `apps.deliveries.services.generate_delivery_requests_for_recurring_route`,
    which is a deliberate, documented `NotImplementedError` stub, and
    docs/CURRENT_STATUS.md's "Known gaps" for the full disclosure.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="recurring_routes"
    )
    name = models.CharField(max_length=200)
    frequency = models.CharField(max_length=16, choices=RecurrenceFrequency.choices)
    weekly_days_of_week = models.JSONField(
        default=list,
        blank=True,
        help_text="List of weekday ints (0=Monday..6=Sunday, matching facilities.DayOfWeek). "
        "Only meaningful when frequency=weekly.",
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Open-ended when blank.")
    holiday_exceptions = models.JSONField(
        default=list, blank=True, help_text="Simple list of ISO date strings to skip generation on."
    )
    service_level = models.CharField(max_length=16, choices=ServiceLevel.choices)
    cargo_class = models.ForeignKey(
        "cargo.CargoClass", on_delete=models.PROTECT, related_name="recurring_routes"
    )
    temperature_profile = models.ForeignKey(
        "cargo.TemperatureProfile", on_delete=models.PROTECT, related_name="recurring_routes"
    )
    is_approved = models.BooleanField(
        default=False, help_text="Operations-approval flag — required before generation may run."
    )
    is_paused = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_recurring_routes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization})"


class RecurringRouteStop(models.Model):
    """One stop within a `RecurringRoute`'s multi-stop path."""

    recurring_route = models.ForeignKey(
        RecurringRoute, on_delete=models.CASCADE, related_name="stops"
    )
    sequence = models.PositiveIntegerField()
    stop_type = models.CharField(max_length=16, choices=StopType.choices)
    facility = models.ForeignKey(
        "facilities.Facility", on_delete=models.PROTECT, related_name="recurring_route_stops"
    )
    instructions = models.TextField(blank=True)

    class Meta:
        ordering = ["recurring_route_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["recurring_route", "sequence"],
                name="unique_recurring_route_stop_sequence",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_stop_type_display()} @ {self.facility} ({self.recurring_route_id})"
