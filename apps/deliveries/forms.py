"""The delivery-request "wizard" form.

Design decision — single Django form, not a literal multi-step wizard UI
(see docs/CURRENT_STATUS.md "Phase 2" section, "wizard vs. single form", for
the full write-up): docs/PRODUCT_REQUIREMENTS.md section 5 describes the
wizard by its *required field list*, not by a mandated multi-page UI, and
docs/IMPLEMENTATION_ROADMAP.md's Phase 2 acceptance criteria are about
validation/blocking behavior, not step count. A real multi-step client-side
wizard is exactly the kind of UI-polish investment Phase 8 ("unified design
system... accessibility pass") is for; Phase 2's job is to prove every
required field is captured and every hard validation rule (missing cargo
classification/packaging attestation, prohibited-cargo keywords) is
enforced, which a single full-page form does end-to-end via real HTTP
POSTs, same precedent as Phase 1's Organization/Facility CRUD forms.

`organization` is never a form field, matching every other form in this
codebase (see apps/facilities/forms.py's docstring) — the view sets it from
a permission-checked URL segment.
"""

from __future__ import annotations

from typing import Any

from django import forms

from apps.cargo.models import CargoClass, TemperatureProfile
from apps.deliveries.models import RecipientVerificationMethod, ServiceLevel
from apps.facilities.models import Facility


class DeliveryRequestForm(forms.Form):
    # Pickup/destination — see apps/deliveries/views.py for how each queryset is scoped
    # (pickup restricted to the requesting organization's own facilities; destination is
    # any active facility, since B2B deliveries routinely cross organizations, e.g. an
    # urgent-care clinic sending specimens to a different organization's lab).
    pickup_facility = forms.ModelChoiceField(queryset=Facility.objects.none())
    destination_facility = forms.ModelChoiceField(queryset=Facility.objects.filter(is_active=True))

    pickup_window_start = forms.DateTimeField()
    pickup_window_end = forms.DateTimeField()
    required_delivery_by = forms.DateTimeField()
    service_level = forms.ChoiceField(choices=ServiceLevel.choices)

    cargo_class = forms.ModelChoiceField(queryset=CargoClass.objects.filter(is_active=True))
    package_count = forms.IntegerField(min_value=1, initial=1)
    approximate_weight_kg = forms.DecimalField(required=False, min_value=0)
    approximate_length_cm = forms.DecimalField(required=False, min_value=0)
    approximate_width_cm = forms.DecimalField(required=False, min_value=0)
    approximate_height_cm = forms.DecimalField(required=False, min_value=0)
    temperature_profile = forms.ModelChoiceField(
        queryset=TemperatureProfile.objects.filter(is_active=True)
    )

    sender_contact_name = forms.CharField(max_length=200)
    sender_contact_phone = forms.CharField(max_length=32, required=False)
    sender_contact_role = forms.CharField(max_length=120, required=False)
    recipient_contact_name = forms.CharField(max_length=200)
    recipient_contact_phone = forms.CharField(max_length=32, required=False)
    recipient_contact_role = forms.CharField(max_length=120, required=False)

    recipient_verification_method = forms.ChoiceField(
        choices=RecipientVerificationMethod.choices,
        initial=RecipientVerificationMethod.NONE,
    )
    facility_instructions = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 2000}),
        required=False,
        max_length=2000,
    )

    attest_packaging = forms.BooleanField(
        required=False,
        label="I attest that packaging/classification/sealing meets MedRelay policy for this class",
    )
    attestation_notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2, "maxlength": 2000}),
        required=False,
        max_length=2000,
        label="Attestation notes (optional)",
    )

    def __init__(self, *args: Any, organization: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        pickup_facility_field = self.fields["pickup_facility"]
        assert isinstance(pickup_facility_field, forms.ModelChoiceField)
        pickup_facility_field.queryset = Facility.objects.filter(
            organization=organization, is_active=True
        )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        start = cleaned_data.get("pickup_window_start")
        end = cleaned_data.get("pickup_window_end")
        if start and end and end <= start:
            self.add_error("pickup_window_end", "Pickup window end must be after its start.")
        return cleaned_data
