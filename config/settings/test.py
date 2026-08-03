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
