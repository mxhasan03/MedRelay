"""AppConfig for the dispatch app.

Phase 4 (docs/IMPLEMENTATION_ROADMAP.md "Phase 4 — Dispatch and operations
console"): dispatch recommendations/scoring, job offers, courier assignments,
dispatcher overrides, synthetic route plans, and SLA target profiles. See
`apps.dispatch.models`/`apps.dispatch.services` and docs/CURRENT_STATUS.md
"Phase 4" section.
"""

from django.apps import AppConfig


class DispatchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dispatch"
    label = "dispatch"
    verbose_name = "Dispatch"
