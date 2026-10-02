import os

# Development mode before settings import, so the placeholder-SECRET_KEY guard does not trip.
os.environ.setdefault("APP_ENV", "development")

from config.dburl import parse_database_url  # noqa: E402
from config.settings import *  # noqa: F401, F403, E402

# Two test databases, chosen by MINTY_TEST_PG_URI:
#
#   unset  -> SQLite in memory, tables built FROM THE MODELS (SHARED_MODELS_MANAGED_FOR_TESTING).
#             Proves this service's logic. Cannot see whether the models match the real schema.
#   set    -> PostgreSQL, a database built FROM docs/schema/01_schema_rebased.sql in the Minty repo
#             by tests/pg_harness.py (loaded by conftest.py at the repo root). Nothing is created
#             from the models; a mirror column the schema lacks fails on the SELECT, which is the
#             point. Same knobs as Minty: MINTY_TEST_PG_DBNAME (minty_test), MINTY_TEST_PG_KEEP=1,
#             PG_BIN, MINTY_REPO (C:\Github\Minty). The URI may carry ?schema= like
#             DATABASE_URL (config/dburl.py); it then replaces DB_SCHEMA for the run.
_PG_URI = os.environ.get("MINTY_TEST_PG_URI")
if _PG_URI:
    _db, DB_SCHEMA = parse_database_url(_PG_URI)
    _dbname = os.environ.get("MINTY_TEST_PG_DBNAME", "minty_test")
    DATABASES = {
        "default": {
            **_db,
            "NAME": _dbname,
            "USER": _db["USER"] or "postgres",
            "HOST": _db["HOST"] or "localhost",
            # Never let pytest-django create/destroy a database of its own here; the
            # root conftest overrides django_db_setup and hands it the harness's build.
            "TEST": {"NAME": _dbname},
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
