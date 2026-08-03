"""Forms for the minimal Facility CRUD UI.

`organization` is deliberately not a form field — the view sets it from a
permission-checked URL segment (see apps.facilities.views), never from
client-submitted form data.
"""

from __future__ import annotations

from django import forms

from apps.facilities.models import Facility


class FacilityForm(forms.ModelForm):
    class Meta:
        model = Facility
        fields = [
            "name",
            "facility_type",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "borough",
            "latitude",
            "longitude",
            "service_zone",
            "timezone",
            "access_instructions",
            "verification_requirements",
            "is_active",
        ]
        widgets = {
            "access_instructions": forms.Textarea(attrs={"rows": 3}),
            "verification_requirements": forms.Textarea(attrs={"rows": 3}),
        }
