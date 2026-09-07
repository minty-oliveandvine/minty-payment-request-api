import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# Deliberately no django.contrib.contenttypes / django.contrib.auth.
#
# Nothing here uses them: authentication is our own bearer scheme (core.auth)
# and authorisation our own role checks (core.permissions). There is no admin,
# no sessions, no ContentType lookups. Installing them only made `migrate`
# want to CREATE TABLE django_content_type / auth_* inside pettycashv2 — a
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
# CORS — allow the frontend to call the API from the browser
# ---------------------------------------------------------------------------
FRONTEND_APP_URL = os.environ.get("FRONTEND_APP_URL", "http://localhost:3000")

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", FRONTEND_APP_URL).split(",")
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
# Database — shared with Module 1 Flask app (pettycashv2 schema)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "postgres"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "admin"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "OPTIONS": {"options": "-c search_path=pettycashv2,public"},
    }
}

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
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_KEY = os.environ.get("S3_KEY", "")
S3_SECRET = os.environ.get("S3_SECRET", "")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")

# ---------------------------------------------------------------------------
# Xero OAuth
#
# Intentionally empty. Xero rotates refresh tokens on every use and invalidates
# the previous one, so only ONE service may ever call /connect/token. That service
# is the Flask app. Populating these makes billing a second refresher, which will
# brick the Xero connection until a user manually reconnects. To obtain a fresh
# token, billing calls the Flask app (see XERO_TOKEN_SERVICE_URL below).
# ---------------------------------------------------------------------------
XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# Cross-module
# ---------------------------------------------------------------------------
FLASK_APP_URL = os.environ.get("FLASK_APP_URL", "http://localhost:5001")
# FRONTEND_APP_URL is defined above (CORS section)

# Flask app's internal token endpoint. Authenticated with the shared SECRET_KEY.
XERO_TOKEN_SERVICE_URL = os.environ.get(
    "XERO_TOKEN_SERVICE_URL",
    f"{FLASK_APP_URL}/api/internal/xero/token",
)
XERO_TOKEN_SERVICE_TIMEOUT = int(os.environ.get("XERO_TOKEN_SERVICE_TIMEOUT", "15"))

# ---------------------------------------------------------------------------
# Logging — core + API formatters
# ---------------------------------------------------------------------------
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
            "level": "INFO",
        },
        "minty-api.http": {
            "handlers": ["console_api", "file_api"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
