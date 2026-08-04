"""Seed synthetic, illustrative min/max temperature ranges for the two
existing `TemperatureProfile` reference rows (Phase 6).

These are demo/reference values only (a typical ambient room-temperature
band and a typical 2-8C cold-chain refrigerated band) — not a medically
validated cold-chain specification, per docs/PRODUCT_REQUIREMENTS.md section
12 ("no claim of validated cold-chain compliance in the prototype"). Used by
`apps.temperature.services.record_reading` to decide whether a simulated
reading is an excursion.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import migrations

RANGES = {
    "ambient": {"min_temp_c": Decimal("15.0"), "max_temp_c": Decimal("25.0")},
    "refrigerated": {"min_temp_c": Decimal("2.0"), "max_temp_c": Decimal("8.0")},
}


def seed(apps, schema_editor):
    TemperatureProfile = apps.get_model("cargo", "TemperatureProfile")
    for code, bounds in RANGES.items():
        TemperatureProfile.objects.filter(code=code).update(**bounds)


def unseed(apps, schema_editor):
    TemperatureProfile = apps.get_model("cargo", "TemperatureProfile")
    for code in RANGES:
        TemperatureProfile.objects.filter(code=code).update(min_temp_c=None, max_temp_c=None)


class Migration(migrations.Migration):
    dependencies = [
        ("cargo", "0007_packageconditioncheck_custody_event_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
