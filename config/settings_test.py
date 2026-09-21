import os

from config.settings import *  # noqa: F401, F403

# Token refresh tests call Xero identity URL with Basic auth (mocked).
XERO_CLIENT_ID = "test-xero-client-id"
XERO_CLIENT_SECRET = "test-xero-client-secret"

# Two test databases, chosen by MINTY_TEST_PG_URI:
#
#   unset  -> SQLite in memory, tables built FROM THE MODELS (SHARED_MODELS_MANAGED_FOR_TESTING).
#             Proves this service's logic. Cannot see whether the models match the real schema.
#   set    -> PostgreSQL, a database built FROM docs/schema/01_schema_rebased.sql in the Minty repo
#             by tests/pg_harness.py (loaded by conftest.py at the repo root). Nothing is created
#             from the models; a mirror column the schema lacks fails on the SELECT, which is the
#             point. Same knobs as Minty: MINTY_TEST_PG_DBNAME (minty_test), MINTY_TEST_PG_KEEP=1,
#             PG_BIN, MINTY_REPO (C:\Github\Minty).
_PG_URI = os.environ.get("MINTY_TEST_PG_URI")
if _PG_URI:
    from urllib.parse import urlsplit as _urlsplit

    _u = _urlsplit(_PG_URI)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("MINTY_TEST_PG_DBNAME", "minty_test"),
            "USER": _u.username or "postgres",
            "PASSWORD": _u.password or "",
            "HOST": _u.hostname or "localhost",
            "PORT": str(_u.port or 5432),
            "OPTIONS": {"options": f"-c search_path={DB_SCHEMA},public"},
            # Never let pytest-django create/destroy a database of its own here; the
            # root conftest overrides django_db_setup and hands it the harness's build.
            "TEST": {"NAME": os.environ.get("MINTY_TEST_PG_DBNAME", "minty_test")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING["handlers"]["file_core"] = {"class": "logging.NullHandler"}
LOGGING["handlers"]["file_api"] = {"class": "logging.NullHandler"}

# Force shared_models tables to be created in the test database.
# In production these are managed by the Flask app's Alembic migrations.
SHARED_MODELS_MANAGED_FOR_TESTING = not _PG_URI  # tables come from the schema file on Postgres
