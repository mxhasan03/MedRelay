"""Seed synthetic, admin-editable `SLAProfile` rows, one per
`apps.deliveries.models.ServiceLevel` value.

`min_slack_minutes` values are synthetic prototype defaults (tighter for
faster service levels) — editable afterward from the Django admin without a
code change or migration, mirroring
`apps/deliveries/migrations/0002_seed_pricing_rules.py`'s exact pattern.
"""

from __future__ import annotations

from django.db import migrations

SLA_PROFILES = [
    ("scheduled", 90, "Scheduled deliveries — generous buffer before required_delivery_by."),
    ("same_day", 45, "Same-day deliveries — moderate buffer."),
    ("stat", 15, "STAT deliveries — tight buffer; flagged at-risk quickly."),
]


def seed(apps, schema_editor):
    SLAProfile = apps.get_model("dispatch", "SLAProfile")
    for service_level, min_slack_minutes, description in SLA_PROFILES:
        SLAProfile.objects.get_or_create(
            service_level=service_level,
            defaults={"min_slack_minutes": min_slack_minutes, "description": description},
        )


def unseed(apps, schema_editor):
    SLAProfile = apps.get_model("dispatch", "SLAProfile")
    SLAProfile.objects.filter(service_level__in=[s for s, _, _ in SLA_PROFILES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dispatch", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
