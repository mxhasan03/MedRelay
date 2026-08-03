"""Test settings.

Phase 0 has no PostGIS-specific models yet, so SQLite is deliberately allowed
here for speed and zero external service requirements in CI, per
docs/TECH_STACK_AND_ZERO_COST_POLICY.md ("SQLite allowed only for limited
unit tests when PostgreSQL-specific behavior is not being tested"). Real
Postgres/PostGIS behavior must be exercised against the compose `db` service
once spatial or Postgres-specific models exist.
"""

from .base import *  # noqa: F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "medrelay-test-cache",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Phase 5 is the first phase whose templates actually render a {% static %}
# tag during a test run (the courier PWA's manifest link/icon/JS includes).
# config/settings/base.py's STORAGES uses whitenoise's
# CompressedManifestStaticFilesStorage for "staticfiles", which requires a
# real `collectstatic` run to have already built its hashed-filename
# manifest -- appropriate for a real deployment, but not something the test
# suite runs (and shouldn't need to, for a fast, hermetic unit/integration
# test run). Test settings use the plain, non-manifest StaticFilesStorage
# instead, so {% static %} tags resolve directly against
# STATICFILES_DIRS/static/ without requiring collectstatic.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
