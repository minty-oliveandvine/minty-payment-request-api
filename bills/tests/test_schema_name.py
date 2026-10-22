"""The schema name is a setting: ``config.settings.DB_SCHEMA``, read from ``?schema=`` on
``DATABASE_URL`` (Minty reads its own URL the same way). No application string may carry it;
``search_path`` and the raw queries read the setting. Comments and docstrings are free to say it."""

from __future__ import annotations

from pathlib import Path

from minty_shared.config import django_database
from minty_shared.testing import guards

ROOT = Path(__file__).resolve().parents[2]


def test_no_application_string_carries_the_schema_name():
    hits = guards.string_literals(ROOT, "pettycashv3", skip=("tests/", "bills/tests/", "migrations/"))
    assert hits == [], "read settings.DB_SCHEMA instead of spelling the schema"


def test_search_path_follows_the_schema_in_the_url():
    db, schema = django_database("postgresql://u:p@db:5432/minty?schema=pettycash_alt")
    assert schema == "pettycash_alt"
    assert db["OPTIONS"]["options"] == "-c search_path=pettycash_alt,public"
