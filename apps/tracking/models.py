"""Courier location pings — Phase 5 (courier PWA and tracking).

`docs/ARCHITECTURE_AND_DATA_MODEL.md`'s "Couriers" entity list names
`CourierLocationPing`, deferred by Phase 3 ("belongs to Phase 5 (tracking)
work; not built" — see docs/CURRENT_STATUS.md's Phase 3 "Known gaps") and
referenced again by Phase 4's scoring/SLA write-up ("there is still no
courier-location model... `CourierLocationPing` remains Phase 5 work"). This
is that model.

Geo storage: plain `DecimalField` lat/lng, matching Phase 1's `Facility`
precedent exactly (docs/CURRENT_STATUS.md "Phase 1" design decision 1) — no
PostGIS `PointField`. The same reasoning still holds: there is still no
geo-distance *query* anywhere in this codebase (nearest-courier search,
radius lookups) that would need spatial indexing; a ping is just a stored
coordinate read back for display. `config.settings.test` still runs on plain
SQLite, which cannot execute PostGIS spatial queries at all — introducing
GeoDjango now, with nothing that queries geometrically, would be the exact
kind of premature complexity Phase 1 declined to add.

Append-only note: unlike `apps.deliveries.models.DeliveryStatusTransition`,
this model does **not** implement ORM-level append-only enforcement
(overridden `save()`/`delete()`/queryset `update()`/`delete()`). That
guarantee exists for `DeliveryStatusTransition` because delivery-status
history is safety/audit-relevant (SLA/incident analysis, chain-of-custody
adjacency). Raw location telemetry is comparatively low-stakes, high-volume
data (a new row every few seconds while a courier is actively en route) —
building the same tamper-evident guard for it here would be needless
ceremony for this prototype's needs. This is a deliberate, documented scope
choice, not an oversight.
"""

from __future__ import annotations

from django.db import models


class CourierLocationPing(models.Model):
    """One browser Geolocation reading, tied to the courier's currently
    active `DeliveryAssignment` (`apps.dispatch.models.DeliveryAssignment`).

    `apps.tracking.services.record_location_ping` is the only intended write
    path — see that module for the hard "location stops after terminal
    state" acceptance criterion this model's existence enables testing.
    """

    assignment = models.ForeignKey(
        "dispatch.DeliveryAssignment", on_delete=models.CASCADE, related_name="location_pings"
    )
    courier = models.ForeignKey(
        "couriers.CourierProfile", on_delete=models.CASCADE, related_name="location_pings"
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy_meters = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional — the browser Geolocation API's reported accuracy radius, if given.",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["assignment", "-recorded_at"])]

    def __str__(self) -> str:
        return f"{self.courier} @ ({self.latitude}, {self.longitude}) [{self.recorded_at}]"
