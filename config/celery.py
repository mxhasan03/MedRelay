"""Celery application for the MedRelay project.

No scheduled/periodic tasks are defined in Phase 0. This wires Celery to
Django settings so the `worker` compose service has something real to run,
and so later phases can add tasks without further plumbing.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("medrelay")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
