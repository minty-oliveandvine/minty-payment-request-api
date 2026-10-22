"""The env surface as config/settings.py reads it: DATABASE_URL, S3_URL, APP_ENV and the
SECRET_KEY guard (their parsers are minty_shared.config, tested there), and the derived URLs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# --- settings.py (fresh interpreter: settings are read once at import) --------------------


PLACEHOLDER_S3_URL = "https://k:s@s3.us-east-1.example.invalid/placeholder"


def _settings(env: dict) -> subprocess.CompletedProcess:
    code = (
        "import json, config.settings as s; print(json.dumps({k: getattr(s, k) for k in "
        "('DEBUG', 'DB_SCHEMA', 'DATABASES', 'S3_BUCKET', 'S3_REGION', 'PETTY_CASH_URL', "
        "'XERO_TOKEN_SERVICE_URL', 'CORS_ALLOWED_ORIGINS', 'CORS_ALLOWED_ORIGIN_REGEXES', "
        "'PAYMENT_REQUEST_WEB_URL')}))"
    )
    clean = {k: v for k, v in os.environ.items() if k not in {
        "APP_ENV", "SECRET_KEY", "DATABASE_URL", "S3_URL", "PETTY_CASH_URL",
        "PAYMENT_REQUEST_WEB_URL", "ONBOARDING_WEB_URL", "CORS_ALLOWED_ORIGINS", "PREVIEW_ORIGIN_REGEX",
    }}
    # cwd outside the repo so load_dotenv() cannot pick up a developer's .env
    return subprocess.run(
        [sys.executable, "-c", code],
        env={**clean, "PYTHONPATH": str(ROOT), "S3_URL": PLACEHOLDER_S3_URL, **env},
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


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_no_s3_url_refuses_to_start_in_every_environment(app_env):
    # an unset S3_URL used to start fine and answer 500 on every upload (2026-10-05)
    proc = _settings({"APP_ENV": app_env, "SECRET_KEY": "real", "S3_URL": ""})
    assert proc.returncode != 0
    assert "ImproperlyConfigured" in proc.stderr and "S3_URL" in proc.stderr


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


def test_preview_origins_are_off_unless_set():
    assert _ok({"APP_ENV": "development"})["CORS_ALLOWED_ORIGIN_REGEXES"] == []
    regex = r"^https://pr-[a-z0-9-]+-(?:payment-request|onboarding)\.dailyminty\.com$"
    s = _ok({"APP_ENV": "production", "SECRET_KEY": "real", "PREVIEW_ORIGIN_REGEX": regex})
    assert s["CORS_ALLOWED_ORIGIN_REGEXES"] == [regex]


def test_a_loose_preview_regex_refuses_to_start():
    proc = _settings({"APP_ENV": "development", "PREVIEW_ORIGIN_REGEX": ".*"})
    assert proc.returncode != 0
    assert "ImproperlyConfigured" in proc.stderr and "PREVIEW_ORIGIN_REGEX" in proc.stderr
