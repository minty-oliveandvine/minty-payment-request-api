"""The env surface: DATABASE_URL (config/dburl.py), S3_URL (config/s3url.py), APP_ENV and
the SECRET_KEY guard, and the derived URLs in config/settings.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.dburl import DEFAULT_DATABASE_URL, database_url, parse_database_url
from config.s3url import parse_s3_url

ROOT = Path(__file__).resolve().parents[2]


# --- DATABASE_URL ------------------------------------------------------------------------


def test_schema_in_the_url_becomes_search_path_and_is_not_passed_to_the_driver():
    db, schema = parse_database_url("postgresql://u:p@db.example:6543/minty?schema=pettycash_alt")
    assert schema == "pettycash_alt"
    assert db == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "minty",
        "USER": "u",
        "PASSWORD": "p",
        "HOST": "db.example",
        "PORT": "6543",
        "OPTIONS": {"options": "-c search_path=pettycash_alt,public"},
    }


def test_schema_defaults_to_pettycashv3_and_port_to_5432():
    db, schema = parse_database_url("postgresql://u@localhost/postgres")
    assert schema == "pettycashv3"
    assert db["OPTIONS"]["options"] == "-c search_path=pettycashv3,public"
    assert db["PORT"] == "5432"
    assert db["PASSWORD"] == ""


def test_other_query_params_are_kept_as_driver_options():
    db, _ = parse_database_url("postgresql://u:p@h/d?sslmode=require&schema=s1&connect_timeout=5")
    assert db["OPTIONS"] == {
        "sslmode": "require",
        "connect_timeout": "5",
        "options": "-c search_path=s1,public",
    }


def test_user_password_and_dbname_are_percent_decoded():
    db, _ = parse_database_url("postgresql://us%40er:p%40ss%3Aw%2Frd@h:5432/my%20db")
    assert (db["USER"], db["PASSWORD"], db["NAME"]) == ("us@er", "p@ss:w/rd", "my db")


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"])
def test_accepted_schemes(scheme):
    db, _ = parse_database_url(f"{scheme}://u:p@h:5432/d")
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert db["NAME"] == "d"


def test_other_schemes_are_refused():
    with pytest.raises(ValueError):
        parse_database_url("mysql://u:p@h/d")


def test_database_url_default_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() == DEFAULT_DATABASE_URL == "postgresql://postgres@localhost:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", "postgres://a@b/c")
    assert database_url() == "postgres://a@b/c"


# --- S3_URL ------------------------------------------------------------------------------


def test_s3_url_backblaze_region_from_host():
    s3 = parse_s3_url("https://KEYID:se%2Fcr%2Bet@s3.eu-central-003.backblazeb2.com/my-bucket")
    assert s3 == {
        "endpoint_url": "https://s3.eu-central-003.backblazeb2.com",
        "bucket": "my-bucket",
        "key": "KEYID",
        "secret": "se/cr+et",
        "region": "eu-central-003",
    }


def test_s3_url_region_query_wins_and_default_region():
    assert parse_s3_url("https://k:s@s3.us-west-004.backblazeb2.com/b?region=x-1")["region"] == "x-1"
    assert parse_s3_url("http://k:s@minio:9000/b")["region"] == "us-east-1"
    assert parse_s3_url("http://k:s@minio:9000/b")["endpoint_url"] == "http://minio:9000"


def test_s3_url_unset():
    assert parse_s3_url("") == {"endpoint_url": "", "bucket": "", "key": "", "secret": "", "region": "us-east-1"}


# --- settings.py (fresh interpreter: settings are read once at import) --------------------


def _settings(env: dict) -> subprocess.CompletedProcess:
    code = (
        "import json, config.settings as s; print(json.dumps({k: getattr(s, k) for k in "
        "('DEBUG', 'DB_SCHEMA', 'DATABASES', 'S3_BUCKET', 'S3_REGION', 'PETTY_CASH_URL', "
        "'XERO_TOKEN_SERVICE_URL', 'CORS_ALLOWED_ORIGINS', 'PAYMENT_REQUEST_WEB_URL')}))"
    )
    clean = {k: v for k, v in os.environ.items() if k not in {
        "APP_ENV", "SECRET_KEY", "DATABASE_URL", "S3_URL", "PETTY_CASH_URL",
        "PAYMENT_REQUEST_WEB_URL", "ONBOARDING_WEB_URL", "CORS_ALLOWED_ORIGINS",
    }}
    # cwd outside the repo so load_dotenv() cannot pick up a developer's .env
    return subprocess.run(
        [sys.executable, "-c", code],
        env={**clean, "PYTHONPATH": str(ROOT), **env},
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(env: dict) -> dict:
    proc = _settings(env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_production_refuses_the_placeholder_secret_key():
    proc = _settings({})  # APP_ENV unset -> production
    assert proc.returncode != 0
    assert "ImproperlyConfigured" in proc.stderr and "SECRET_KEY" in proc.stderr
    assert _settings({"APP_ENV": "staging"}).returncode != 0  # unknown -> production


def test_app_env_drives_debug():
    assert _ok({"APP_ENV": "development"})["DEBUG"] is True
    assert _ok({"APP_ENV": "production", "SECRET_KEY": "real"})["DEBUG"] is False


def test_settings_read_database_url_and_s3_url():
    s = _ok({
        "APP_ENV": "development",
        "DATABASE_URL": "postgresql://u:p@db:5432/minty?schema=pettycash_alt&sslmode=require",
        "S3_URL": "https://k:s@s3.us-west-004.backblazeb2.com/bucket-1",
    })
    assert s["DB_SCHEMA"] == "pettycash_alt"
    assert s["DATABASES"]["default"]["OPTIONS"] == {
        "sslmode": "require",
        "options": "-c search_path=pettycash_alt,public",
    }
    assert (s["S3_BUCKET"], s["S3_REGION"]) == ("bucket-1", "us-west-004")


def test_derived_urls_and_cors_default():
    s = _ok({"APP_ENV": "development", "PETTY_CASH_URL": "http://minty:8010/"})
    assert s["PETTY_CASH_URL"] == "http://minty:8010"
    assert s["XERO_TOKEN_SERVICE_URL"] == "http://minty:8010/api/internal/xero/token"
    assert s["CORS_ALLOWED_ORIGINS"] == ["http://localhost:3020", "http://localhost:3030"]
    s = _ok({"APP_ENV": "development", "CORS_ALLOWED_ORIGINS": "https://a.example, https://b.example/"})
    assert s["CORS_ALLOWED_ORIGINS"] == ["https://a.example", "https://b.example"]
