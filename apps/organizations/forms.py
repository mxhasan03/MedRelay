"""Forms for the minimal Organization CRUD UI.

Deliberately excludes `organization` identity/ownership from any client-
editable field — the organization a facility/membership belongs to is always
set by the view from a permission-checked URL segment, never trusted from
submitted form data (see apps.organizations.services module docstring).
"""

from __future__ import annotations

from django import forms

from apps.organizations.models import Organization


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "org_type", "is_active", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
