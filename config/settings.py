import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from minty_shared.config import (
    DEFAULT_LOCAL_DATABASE_URL,
    app_env,
    database_url_from_env,
    django_database,
    parse_s3_url,
    preview_origin_regexes,
    require_secret_key,
)
from minty_shared.logctx import logging_config

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# APP_ENV: "development" or "production"; anything else is production (minty_shared.config).
APP_ENV = app_env()
DEBUG = APP_ENV == "development"

_DEFAULT_SECRET_KEY = "change-me-in-production"

# REFUSE TO BOOT WITH THE PLACEHOLDER KEY OUTSIDE DEVELOPMENT (minty_shared.config). Without the
# shared key every authenticated call answers 401 while public endpoints keep working, and the
# placeholder is in the repo, so tokens signed with it are forgeable.
try:
    SECRET_KEY = require_secret_key(
        os.environ.get("SECRET_KEY"), placeholder=_DEFAULT_SECRET_KEY, development=DEBUG
    )
except RuntimeError as exc:
    raise ImproperlyConfigured(str(exc)) from None
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
    "minty_shared.django.middleware.RequestLoggingMiddleware",
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
# A branch's preview set (minty_shared.config); unset = off, a loose pattern refuses to boot.
try:
    CORS_ALLOWED_ORIGIN_REGEXES = preview_origin_regexes(os.environ.get("PREVIEW_ORIGIN_REGEX"))
except ValueError as exc:
    raise ImproperlyConfigured(str(exc)) from None
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
# (minty_shared.config). DB_SCHEMA is the schema every model lives in, shared with Minty, which
# reads ?schema= from its own DATABASE_URL the same way. pettycashv3 is the permanent
# production name; it is a setting, not a literal - every db_table is unqualified and
# resolves through search_path, and the raw queries read this. The test settings and the
# root conftest read it too, so `?schema=pettycash_alt` on the test URL proves it.
_DEFAULT_DB, DB_SCHEMA = django_database(database_url_from_env(DEFAULT_LOCAL_DATABASE_URL))
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
# S3_URL = https://KEY:SECRET@s3.<region>.backblazeb2.com/<bucket> (minty_shared.config).
#
# REFUSE TO BOOT WITHOUT A WHOLE S3_URL, in every environment. An unset S3_URL used to leave the
# bucket empty: the API started, and every invoice and bank-slip upload then answered 500
# ("Invalid bucket name") - a .env still carrying the pre-2026-10-02 S3_* names did exactly that.
_S3_URL = (os.environ.get("S3_URL") or "").strip()
if not _S3_URL:
    raise ImproperlyConfigured(
        "S3_URL is not set (https://KEY:SECRET@s3.<region>.backblazeb2.com/<bucket>) - every "
        "attachment upload and download would fail. Refusing to start."
    )
try:
    _S3 = parse_s3_url(_S3_URL)
except ValueError as exc:
    raise ImproperlyConfigured(str(exc)) from None
S3_BUCKET = _S3.bucket
S3_KEY = _S3.key
S3_SECRET = _S3.secret
S3_REGION = _S3.region
S3_ENDPOINT_URL = _S3.endpoint_url

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
# Logging - one line format for every Minty backend (minty_shared.logctx):
#   <UTC>Z LEVEL [trace=…] [user=…|entity=…|step=…] message
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_DIR = BASE_DIR / "logs"

LOGGING = logging_config(
    {"minty-api": "core.log", "minty-api.http": "api.log"},
    log_dir=LOG_DIR,
    no_propagate=["minty-api.http"],
    level=LOG_LEVEL,
)
# The access line's logger (minty_shared.django.middleware.RequestLoggingMiddleware).
MINTY_HTTP_LOGGER = "minty-api.http"
