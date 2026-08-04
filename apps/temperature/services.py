"""Simulated temperature-reading recording and excursion detection.

`record_reading` is the single entry point real callers (the
`simulate_temperature_readings` management command, or a future courier PWA
"log a reading" action) use — see module docstring in `models.py` for the
"always simulated, never a live device" honesty statement.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.incidents.models import IncidentCategory, IncidentSeverity
from apps.incidents.services import open_incident
from apps.temperature.models import TemperatureExcursion, TemperatureReading

if TYPE_CHECKING:
    import datetime

    from apps.accounts.models import User
    from apps.cargo.models import Package
    from apps.deliveries.models import DeliveryRequest


@transaction.atomic
def record_reading(
    delivery_request: DeliveryRequest,
    *,
    temperature_c: Decimal | float | str,
    package: Package | None = None,
    recorded_at: datetime.datetime | None = None,
    actor: User | None = None,
) -> TemperatureReading:
    """Record one simulated `TemperatureReading` for `delivery_request` (and,
    optionally, a specific `package`).

    If the reading falls outside the applicable `TemperatureProfile`'s
    [min, max] range (`package.temperature_profile` if `package` is given,
    else `delivery_request.temperature_profile`), this creates a
    `TemperatureExcursion` and opens a `SEVERE` `Incident` for it — a real
    business rule, not passive storage (docs/PRODUCT_REQUIREMENTS.md
    section 12: "excursion opens an incident and may place delivery on
    hold"). A profile with no configured range (both bounds blank, e.g.
    ambient with no upper bound) never excursions.
    """
    temperature = Decimal(str(temperature_c))
    reading = TemperatureReading.objects.create(
        delivery_request=delivery_request,
        package=package,
        temperature_c=temperature,
        recorded_at=recorded_at or timezone.now(),
        recorded_by=actor,
    )

    profile = (
        package.temperature_profile if package is not None else delivery_request.temperature_profile
    )
    if profile is not None and not profile.in_range(temperature):
        incident = open_incident(
            delivery_request,
            category=IncidentCategory.TEMPERATURE_EXCURSION,
            severity=IncidentSeverity.SEVERE,
            summary=(
                f"Temperature reading {temperature}C is outside the required "
                f"{profile.name} range ({profile.min_temp_c}C to {profile.max_temp_c}C)."
            ),
            actor=actor,
            package=package,
        )
        TemperatureExcursion.objects.create(
            reading=reading,
            delivery_request=delivery_request,
            package=package,
            temperature_c=temperature,
            threshold_min_c=profile.min_temp_c,
            threshold_max_c=profile.max_temp_c,
            incident=incident,
        )

    return reading


__all__ = ["record_reading"]
