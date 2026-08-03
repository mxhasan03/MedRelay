"""Development settings — used inside the docker compose `web`/`worker` services."""

from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])

# Convenient for local iteration; DEBUG=True already reveals server errors.
INTERNAL_IPS = ["127.0.0.1"]
