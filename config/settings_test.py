import os

# Development mode before settings import, so the placeholder-SECRET_KEY guard does not trip.
os.environ.setdefault("APP_ENV", "development")
# A bucket name for the S3_URL guard; tests never reach a real bucket (they patch the client).
os.environ.setdefault("S3_URL", "https://test-key:test-secret@s3.us-east-1.example.invalid/test-bucket")

from config.dburl import parse_database_url  # noqa: E402
from config.settings import *  # noqa: F401, F403, E402

# Two test databases, chosen by MINTY_TEST_PG_URI:
#
#   unset  -> SQLite in memory, every table built FROM minty-db's models (MIGRATION_MODULES has
#             minty_db: None); bills/models.py's own models stay unmanaged and read the same tables.
#             Proves this service's logic. Cannot see whether the models match the real schema.
#   set    -> PostgreSQL, a database built FROM minty-db's packaged 01_schema_rebased.sql by
#             minty_db.testing.pg (the root conftest.py's plugin). Nothing is created from the
#             models; a model column the schema lacks fails on the SELECT, which is the point.
#             MINTY_TEST_PG_DBNAME (minty_test - always a unique one), MINTY_TEST_PG_KEEP=1.
#             The URI may carry ?schema= like
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

LOGGING["handlers"]["file_core"] = {"class": "logging.NullHandler"}  # noqa: F405
LOGGING["handlers"]["file_api"] = {"class": "logging.NullHandler"}  # noqa: F405

