import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from config.dburl import database_url, parse_database_url
from config.s3url import parse_s3_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# APP_ENV: "development" or "production" (the default; anything unrecognised counts as production).
APP_ENV = os.environ.get("APP_ENV", "production").strip().lower()
DEBUG = APP_ENV == "development"

_DEFAULT_SECRET_KEY = "change-me-in-production"
SECRET_KEY = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)

# REFUSE TO BOOT WITH THE PLACEHOLDER KEY OUTSIDE DEVELOPMENT. Without the shared key every
# authenticated call answers 401 while public endpoints keep working, and the placeholder is
# in the repo, so tokens signed with it are forgeable. A crash at startup is the loud failure.
if not DEBUG and SECRET_KEY == _DEFAULT_SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is not set. It must be the same value the Flask app mints tokens "
        "with; without it every request is refused with 401. Refusing to start."
    )
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# Deliberately no django.contrib.contenttypes / django.contrib.auth.
#
# Nothing here uses them: authentication is our own bearer scheme (core.auth)
# and authorisation our own role checks (core.permissions). There is no admin,
# no sessions, no ContentType lookups. Installing them only made `migrate`
# want to CREATE TABLE django_content_type / auth_* inside pettycashv3 — a
# schema Flask owns — which fails on any database where those tables already
# exist. Keep them out; they buy nothing and only fight Alembic for the schema.
INSTALLED_APPS = [
    "corsheaders",
    "core",
    "shared_models",
    "bills",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "core.middleware.RequestLoggingMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# ---------------------------------------------------------------------------
# Cross-module URLs (trailing slashes stripped)
# ---------------------------------------------------------------------------
PETTY_CASH_URL = os.environ.get("PETTY_CASH_URL", "http://localhost:8010").rstrip("/")
PAYMENT_REQUEST_WEB_URL = os.environ.get("PAYMENT_REQUEST_WEB_URL", "http://localhost:3020").rstrip("/")
ONBOARDING_WEB_URL = os.environ.get("ONBOARDING_WEB_URL", "http://localhost:3030").rstrip("/")

# ---------------------------------------------------------------------------
# CORS — allow the payment-request and onboarding web apps to call the API from the browser.
# CORS_ALLOWED_ORIGINS (comma-separated) overrides the derived default.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", f"{PAYMENT_REQUEST_WEB_URL},{ONBOARDING_WEB_URL}"
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_HEADERS = [
    "authorization",
    "content-type",
    "x-entity-id",
    "accept",
    "origin",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — shared with Module 1 Flask app (pettycashv3 schema)
# ---------------------------------------------------------------------------
# DATABASE_URL = postgresql://user:pass@host:5432/dbname?schema=pettycashv3[&sslmode=...]
# (config/dburl.py). DB_SCHEMA is the schema every model lives in, shared with Minty, which
# reads ?schema= from its own DATABASE_URL the same way. pettycashv3 is the permanent
# production name; it is a setting, not a literal - every db_table is unqualified and
# resolves through search_path, and the raw queries read this. The test settings and the
# root conftest read it too, so `?schema=pettycash_alt` on the test URL proves it.
_DEFAULT_DB, DB_SCHEMA = parse_database_url(database_url())
DATABASES = {"default": _DEFAULT_DB}

# LocMem debounce for Flask chart sync (single-process; replace for multi-worker).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "minty-billing",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
# S3 / Backblaze — for attachment uploads
# ---------------------------------------------------------------------------
# S3_URL = https://KEY:SECRET@s3.<region>.backblazeb2.com/<bucket> (config/s3url.py)
_S3 = parse_s3_url(os.environ.get("S3_URL", "").strip())
S3_BUCKET = _S3["bucket"]
S3_KEY = _S3["key"]
S3_SECRET = _S3["secret"]
S3_REGION = _S3["region"]
S3_ENDPOINT_URL = _S3["endpoint_url"]

# ---------------------------------------------------------------------------
# Xero
#
# No Xero OAuth client credentials here, deliberately. Xero rotates refresh tokens on
# every use and invalidates the previous one, so only ONE service may ever call
# /connect/token: the Flask app. To obtain a fresh token, this service asks the Flask
# app's internal token endpoint, authenticated with the shared SECRET_KEY.
# ---------------------------------------------------------------------------
XERO_TOKEN_SERVICE_URL = f"{PETTY_CASH_URL}/api/internal/xero/token"
XERO_TOKEN_SERVICE_TIMEOUT = 15  # seconds

# ---------------------------------------------------------------------------
# Logging — core + API formatters
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "core": {"()": "core.log_formatters.CoreFormatter"},
        "api": {"()": "core.log_formatters.ApiFormatter"},
    },
    "handlers": {
        "console_core": {
            "class": "logging.StreamHandler",
            "formatter": "core",
        },
        "console_api": {
            "class": "logging.StreamHandler",
            "formatter": "api",
        },
        "file_core": {
            "class": "logging.FileHandler",
            "filename": str(LOG_DIR / "core.log"),
            "formatter": "core",
        },
        "file_api": {
            "class": "logging.FileHandler",
            "filename": str(LOG_DIR / "api.log"),
            "formatter": "api",
        },
    },
    "loggers": {
        "minty-api": {
            "handlers": ["console_core", "file_core"],
            "level": LOG_LEVEL,
        },
        "minty-api.http": {
            "handlers": ["console_api", "file_api"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
