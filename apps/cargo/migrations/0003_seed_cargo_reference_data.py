"""Seed the fixed, non-user-editable cargo/temperature reference data.

Three `CargoClass` rows (docs/PRODUCT_REQUIREMENTS.md section 3) with their
`CargoPolicy`, and two `TemperatureProfile` rows (ambient/refrigerated —
frozen deferred). This is reference/lookup data, not demo-tenant data, so it
lives in a migration (always present, in every environment including CI)
rather than `seed_demo_data` (which is for optional synthetic
organizations/facilities/users).
"""

from __future__ import annotations

from django.db import migrations

CARGO_CLASSES = [
    {
        "code": "class_1",
        "name": "Class 1 — Documents & Non-Hazardous Supplies",
        "description": (
            "Sealed records, PPE, test kits, small devices, equipment parts, and other "
            "non-hazardous supplies."
        ),
        "policy": {
            "requires_packaging_attestation": True,
            "allows_ambient": True,
            "allows_refrigerated": False,
            "notes": "Documents/non-hazardous supplies are not eligible for refrigerated transport.",
        },
    },
    {
        "code": "class_2",
        "name": "Class 2 — Approved Routine Specimens",
        "description": (
            "Only customer-attested, properly classified, packaged, sealed, and labeled routine "
            "specimens supported by written platform policy."
        ),
        "policy": {
            "requires_packaging_attestation": True,
            "allows_ambient": True,
            "allows_refrigerated": True,
            "notes": "Routine specimens may require cold-chain (refrigerated) transport.",
        },
    },
    {
        "code": "class_3",
        "name": "Class 3 — Sealed Non-Controlled Prescription Medication",
        "description": (
            "Pharmacy-prepared medication only. The pharmacy/facility remains responsible for "
            "lawful dispensing, packaging, labeling, and release."
        ),
        "policy": {
            "requires_packaging_attestation": True,
            "allows_ambient": True,
            "allows_refrigerated": True,
            "notes": "Some prescription medications require refrigerated transport.",
        },
    },
]

TEMPERATURE_PROFILES = [
    {"code": "ambient", "name": "Ambient", "description": "No active temperature control."},
    {
        "code": "refrigerated",
        "name": "Refrigerated",
        "description": (
            "Cold-chain transport. Frozen cargo is explicitly deferred per "
            "docs/PRODUCT_REQUIREMENTS.md section 3 and is not a supported profile."
        ),
    },
]


def seed(apps, schema_editor):
    CargoClass = apps.get_model("cargo", "CargoClass")
    CargoPolicy = apps.get_model("cargo", "CargoPolicy")
    TemperatureProfile = apps.get_model("cargo", "TemperatureProfile")

    for entry in CARGO_CLASSES:
        cargo_class, _ = CargoClass.objects.get_or_create(
            code=entry["code"],
            defaults={"name": entry["name"], "description": entry["description"]},
        )
        CargoPolicy.objects.get_or_create(cargo_class=cargo_class, defaults=entry["policy"])

    for entry in TEMPERATURE_PROFILES:
        TemperatureProfile.objects.get_or_create(
            code=entry["code"],
            defaults={"name": entry["name"], "description": entry["description"]},
        )


def unseed(apps, schema_editor):
    CargoClass = apps.get_model("cargo", "CargoClass")
    TemperatureProfile = apps.get_model("cargo", "TemperatureProfile")
    CargoClass.objects.filter(code__in=[e["code"] for e in CARGO_CLASSES]).delete()
    TemperatureProfile.objects.filter(code__in=[e["code"] for e in TEMPERATURE_PROFILES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cargo", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
