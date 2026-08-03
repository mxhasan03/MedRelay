"""Production-shaped settings.

No production deployment target is defined for this prototype yet — this
module exists so the settings package has the standard base/dev/prod/test
shape from day one, and so future phases have a clear place to harden
security settings. DEMO_MODE remains the only supported operating mode; see
docs/SECURITY_COMPLIANCE_BOUNDARIES.md before ever pointing this at anything
resembling real data.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
