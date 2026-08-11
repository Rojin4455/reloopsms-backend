"""
Isolated Django settings for local verification.

Uses in-memory SQLite so tests NEVER touch the production RDS from .env.
"""

from reloopsms.settings import *  # noqa: F403


class _DisableMigrations:
    """Build tables from current models; skip historical data migrations."""

    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIGRATION_MODULES = _DisableMigrations()

# Run Celery tasks inline; do not talk to Redis/broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# Keep DEBUG True so any accidental Stripe call would use test key — tests still mock Stripe.
DEBUG = True

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
