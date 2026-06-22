from config.settings import *  # noqa: F401, F403

# Token refresh tests call Xero identity URL with Basic auth (mocked).
XERO_CLIENT_ID = "test-xero-client-id"
XERO_CLIENT_SECRET = "test-xero-client-secret"

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
SHARED_MODELS_MANAGED_FOR_TESTING = True
