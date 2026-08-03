"""Base Django settings shared by all environments.

MedRelay (medical-courier-platform) Phase 0 — repository foundation only.
This is a synthetic-data-only prototype. It is not certified or approved for
real medical delivery operations and does not claim HIPAA, OSHA, DOT,
pharmacy, employment, or other legal compliance. See docs/CLAUDE.md and
docs/SECURITY_COMPLIANCE_BOUNDARIES.md.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# Explicit demo-only operating mode. A future PILOT_MODE would require the
# professional review gates documented in docs/SECURITY_COMPLIANCE_BOUNDARIES.md
# and is intentionally not implemented in this repository.
APP_MODE = env.str("APP_MODE", default="DEMO_MODE")

# .env.example intentionally ships DJANGO_SECRET_KEY empty (names only, no real values), so an
# empty string is treated the same as "unset" and falls back to an obviously-fake dev-only value.
SECRET_KEY = (
    env.str("DJANGO_SECRET_KEY", default="") or "django-insecure-dev-only-secret-key-change-me"
)

DEBUG = env.bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "drf_spectacular",
    # MedRelay modular monolith apps (Phase 0: no domain models yet).
    "apps.accounts",
    "apps.organizations",
    "apps.facilities",
    "apps.couriers",
    "apps.cargo",
    "apps.deliveries",
    "apps.dispatch",
    "apps.custody",
    "apps.tracking",
    "apps.temperature",
    "apps.incidents",
    "apps.notifications",
    "apps.billing",
    "apps.reporting",
    "apps.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.app_mode",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database (overridden per-environment; base defines a plain Postgres default
# for anything that imports base.py directly).
#
# NOTE: the compose "db" service runs the postgis/postgis image so the real
# database is PostGIS-capable, but Phase 0 has no spatial models yet, so the
# Django database ENGINE here is the plain "django.db.backends.postgresql"
# backend (via the "postgres://" URL scheme) rather than
# "django.contrib.gis.db.backends.postgis". This deliberately avoids a hard
# GDAL/GEOS dependency until spatial models are actually introduced in a
# later phase. See docs/CURRENT_STATUS.md.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://medrelay:medrelay@localhost:5432/medrelay",  # pragma: allowlist secret
    )
}
DATABASES["default"].setdefault("CONN_MAX_AGE", 60)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # All timestamps stored in UTC; displayed in America/New_York (see below).
DISPLAY_TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user model, introduced in Phase 1 (docs/IMPLEMENTATION_ROADMAP.md) at the first point any
# app has migrations — the standard, low-risk time to do this per Django's own recommendation to
# never swap AUTH_USER_MODEL mid-project. See apps/accounts/models.py.
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "organization-list"
LOGOUT_REDIRECT_URL = "login"

# Cache / Celery broker (Valkey, Redis-protocol-compatible).
REDIS_URL = env.str("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# Celery
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True

# Email (local Mailpit SMTP capture; never a paid email provider).
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env.str("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=1025)
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False
DEFAULT_FROM_EMAIL = "no-reply@medrelay.demo"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MedRelay API",
    "DESCRIPTION": (
        "MedRelay is a synthetic-data-only software prototype. It is not certified or approved "
        "for real medical delivery operations and does not claim HIPAA, OSHA, DOT, pharmacy, "
        "employment, or other legal compliance."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
