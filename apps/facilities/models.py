"""Customer facilities, contacts, receiving rules, and service zones.

Geo storage decision (see docs/CURRENT_STATUS.md "Phase 1" section for the
full write-up): `Facility.latitude`/`Facility.longitude` are plain
`DecimalField`s, *not* a PostGIS `PointField`. Phase 0 deliberately kept the
Django database `ENGINE` as plain `django.db.backends.postgresql` (see
`config/settings/base.py`) because it had zero spatial models. Phase 1 is
the first phase with a real geographic need (facility coordinates, service
zones), but it still has no geo-distance *queries* — those start in Phase 4
(dispatch/matching, docs/IMPLEMENTATION_ROADMAP.md). Introducing GeoDjango
now would mean a hard GDAL/GEOS system dependency (Dockerfile + CI) for zero
present benefit, and CI's test settings (`config.settings.test`) run on
plain SQLite, which cannot execute PostGIS spatial queries at all — so
"introduce PostGIS" would also force a CI redesign (either a real Postgres/
PostGIS service container in CI, or SpatiaLite for SQLite) before there is
any spatial *behavior* to test. Plain decimal lat/lng needs none of that: it
round-trips through SQLite fine, stores exact coordinates, and is trivially
upgradable to a PostGIS `PointField` later (a straightforward migration)
once Phase 4 actually performs geo-distance dispatch logic. This is a
conscious, documented deviation from the letter of
docs/ARCHITECTURE_AND_DATA_MODEL.md's PostGIS mention, not a silent one.
"""

from __future__ import annotations

from typing import Any

from django.db import models


class FacilityType(models.TextChoices):
    """What kind of site a facility physically is.

    Deliberately kept as its own enum (rather than reusing
    `apps.organizations.models.OrganizationType` directly) so a facility's
    physical type can diverge from its owning organization's type — e.g. a
    hospital system `Organization` might have a `LAB_DRAW_SITE` facility.
    """

    CLINIC_SITE = "clinic_site", "Clinic Site"
    URGENT_CARE_SITE = "urgent_care_site", "Urgent Care Site"
    LAB_DRAW_SITE = "lab_draw_site", "Lab Draw / Collection Site"
    LAB_PROCESSING_SITE = "lab_processing_site", "Lab Processing Site"
    PHARMACY_COUNTER = "pharmacy_counter", "Pharmacy Counter"
    HOSPITAL_DOCK = "hospital_dock", "Hospital Receiving Dock"
    HOME_HEALTH_OFFICE = "home_health_office", "Home Health Office"
    OTHER = "other", "Other"


class Borough(models.TextChoices):
    """Service-area boroughs (docs/PRODUCT_REQUIREMENTS.md section 2: "Controlled
    Manhattan-Brooklyn service zone"). `OTHER` exists only so out-of-zone data
    can be represented/rejected explicitly rather than crammed into a wrong
    choice; the prototype does not route deliveries there.
    """

    MANHATTAN = "manhattan", "Manhattan"
    BROOKLYN = "brooklyn", "Brooklyn"
    OTHER = "other", "Other (out of service zone)"


class ServiceZone(models.Model):
    """A named coverage area within the Manhattan-Brooklyn service zone.

    Phase 1 models this as reference data only (name/borough/description) —
    no geometry, no dispatch-eligibility logic yet. Facilities may
    optionally be tagged with a zone; later phases can use it for
    eligibility/routing without changing this table's shape much.
    """

    name = models.CharField(max_length=120, unique=True)
    borough = models.CharField(max_length=16, choices=Borough.choices)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["borough", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_borough_display()})"


class FacilityQuerySet(models.QuerySet["Facility"]):
    def for_user(self, user: Any) -> Any:
        from apps.organizations.services import scope_queryset_to_user_orgs

        return scope_queryset_to_user_orgs(self, user, org_field="organization_id")


class Facility(models.Model):
    """A physical location owned by a customer `Organization`.

    Every field here is operational/logistics data (address, hours, access
    notes) — never diagnosis/lab/clinical/SSN/insurance data, per
    docs/SECURITY_COMPLIANCE_BOUNDARIES.md.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="facilities",
    )
    name = models.CharField(max_length=200)
    facility_type = models.CharField(max_length=32, choices=FacilityType.choices)

    # Address.
    address_line1 = models.CharField(max_length=200)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, default="New York")
    state = models.CharField(max_length=2, default="NY")
    postal_code = models.CharField(max_length=10)
    borough = models.CharField(max_length=16, choices=Borough.choices)

    # Coordinates: plain decimal fields, not PostGIS. See module docstring.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    service_zone = models.ForeignKey(
        ServiceZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facilities",
    )

    timezone = models.CharField(
        max_length=64,
        default="America/New_York",
        help_text="IANA timezone name used to interpret this facility's receiving hours.",
    )

    access_instructions = models.TextField(
        blank=True,
        help_text="Operational access notes for couriers, e.g. entrance/loading-dock guidance.",
    )
    verification_requirements = models.TextField(
        blank=True,
        help_text=(
            "Operational verification steps required at handoff, e.g. photo ID check-in at a "
            "front desk. Never a clinical/diagnostic requirement."
        ),
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FacilityQuerySet.as_manager()

    class Meta:
        ordering = ["organization__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_facility_name_per_org",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization})"


class FacilityContact(models.Model):
    """An operational point-of-contact at a facility (not a patient/recipient)."""

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=200)
    title = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["facility__name", "-is_primary", "name"]

    def __str__(self) -> str:
        return f"{self.name} — {self.facility}"


class DayOfWeek(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class FacilityReceivingRule(models.Model):
    """Per-weekday receiving hours and same-day cutoff time for a facility."""

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="receiving_rules")
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    is_closed = models.BooleanField(
        default=False, help_text="If true, this facility does not receive deliveries on this day."
    )
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)
    same_day_cutoff_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Latest local time a same-day pickup/delivery request is accepted for this day.",
    )

    class Meta:
        ordering = ["facility__name", "day_of_week"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "day_of_week"],
                name="unique_receiving_rule_per_facility_per_day",
            )
        ]

    def __str__(self) -> str:
        return f"{self.facility} — {self.get_day_of_week_display()}"
