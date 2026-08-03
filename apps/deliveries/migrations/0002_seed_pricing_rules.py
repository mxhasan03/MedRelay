"""Seed synthetic, configurable `PricingRule` rows for the demo quote engine.

Every amount here is a synthetic prototype value (docs/PRODUCT_REQUIREMENTS.md
section 14: "Use synthetic configurable rules only. Do not connect a real
payment processor.") — editable afterward from the Django admin without a
code change or migration.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import migrations

PRICING_RULES = [
    ("base_fee", Decimal("12.00"), "Flat base fee applied to every delivery request."),
    ("per_km_rate", Decimal("1.75"), "Synthetic per-kilometer distance rate."),
    ("per_minute_rate", Decimal("0.35"), "Synthetic per-minute time rate."),
    ("average_speed_kmh", Decimal("30.00"), "Assumed average travel speed used to derive time from distance."),
    ("same_day_surcharge", Decimal("8.00"), "Surcharge for SAME_DAY service level."),
    ("stat_surcharge", Decimal("25.00"), "Surcharge for STAT service level."),
    ("cargo_class_2_surcharge", Decimal("5.00"), "Cargo/equipment surcharge for Class 2 (specimens)."),
    ("cargo_class_3_surcharge", Decimal("7.50"), "Cargo/equipment surcharge for Class 3 (medication)."),
    ("refrigerated_surcharge", Decimal("10.00"), "Equipment surcharge for refrigerated transport."),
    ("after_hours_surcharge", Decimal("15.00"), "Surcharge for pickups outside standard weekday hours."),
    ("inter_borough_toll_estimate", Decimal("6.00"), "Flat toll estimate when pickup/destination boroughs differ."),
    ("wait_time_placeholder_fee", Decimal("4.00"), "Flat placeholder for facility wait time."),
    ("return_trip_fee", Decimal("20.00"), "Fee applied when a return trip is required."),
]


def seed(apps, schema_editor):
    PricingRule = apps.get_model("deliveries", "PricingRule")
    for key, amount, description in PRICING_RULES:
        PricingRule.objects.get_or_create(
            key=key, defaults={"amount": amount, "description": description}
        )


def unseed(apps, schema_editor):
    PricingRule = apps.get_model("deliveries", "PricingRule")
    PricingRule.objects.filter(key__in=[key for key, _, _ in PRICING_RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("deliveries", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
