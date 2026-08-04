"""Generate synthetic temperature readings for a delivery's refrigerated packages.

**This command simulates sensor data — it does not talk to any real IoT
device.** Per docs/TECH_STACK_AND_ZERO_COST_POLICY.md's zero-cost policy
(external capabilities like temperature sensors need an adapter with a
local/mock implementation, and there is no real sensor hardware anywhere in
this project), this is that mock implementation: a small deterministic-ish
random walk around the package's required temperature range, with a
configurable chance of generating a genuine excursion so the
incident-hold/temperature-excursion pipeline
(`apps.temperature.services.record_reading`) can be demonstrated end to end
without needing a real cold-chain sensor.

Usage:
    python manage.py simulate_temperature_readings <delivery-request-id> \\
        --count 5 --excursion-chance 0.0
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.deliveries.models import DeliveryRequest
from apps.temperature.services import record_reading


class Command(BaseCommand):
    help = (
        "Generate synthetic (simulated, not real-device) temperature readings for a delivery's "
        "packages, honestly documented as a mock TemperatureSensorProvider-style generator."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("delivery_request_id", help="A DeliveryRequest UUID.")
        parser.add_argument(
            "--count", type=int, default=3, help="Number of readings to generate (default: 3)."
        )
        parser.add_argument(
            "--excursion-chance",
            type=float,
            default=0.0,
            help="Probability (0.0-1.0) that a given reading is deliberately generated outside "
            "the required temperature range, to demonstrate the excursion/incident pipeline "
            "(default: 0.0 — always in-range).",
        )
        parser.add_argument(
            "--seed", type=int, default=None, help="Optional random seed for reproducible output."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            delivery_request = DeliveryRequest.objects.get(pk=options["delivery_request_id"])
        except DeliveryRequest.DoesNotExist as exc:
            raise CommandError(
                f"No DeliveryRequest with id={options['delivery_request_id']!r}."
            ) from exc

        rng = random.Random(options["seed"])  # noqa: S311 - synthetic demo data, not security-sensitive
        profile = delivery_request.temperature_profile
        if profile is None:
            raise CommandError(
                f"Delivery request {delivery_request.pk} has no temperature_profile set."
            )

        min_bound = profile.min_temp_c if profile.min_temp_c is not None else Decimal("15.0")
        max_bound = profile.max_temp_c if profile.max_temp_c is not None else Decimal("25.0")

        created = 0
        excursions = 0
        for _ in range(options["count"]):
            is_excursion = rng.random() < options["excursion_chance"]
            if is_excursion:
                # Deliberately outside range, on a random side.
                offset = Decimal(str(round(rng.uniform(1.0, 5.0), 1)))
                temperature = (max_bound + offset) if rng.random() < 0.5 else (min_bound - offset)
            else:
                temperature = Decimal(
                    str(round(rng.uniform(float(min_bound), float(max_bound)), 1))
                )

            reading = record_reading(
                delivery_request,
                temperature_c=temperature,
                recorded_at=timezone.now(),
            )
            created += 1
            if hasattr(reading, "excursion"):
                excursions += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated {created} simulated reading(s) for delivery {delivery_request.pk} "
                f"({excursions} excursion(s) detected, each opening an incident)."
            )
        )
