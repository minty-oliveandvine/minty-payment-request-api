"""Parse ``DATABASE_URL`` into a Django ``DATABASES`` entry plus the schema name.

    postgresql://user:pass@host:5432/dbname?schema=pettycashv3&sslmode=require

``schema`` is ours, not libpq's: it is popped from the query and becomes ``search_path``
(and ``settings.DB_SCHEMA``). Every other query parameter is handed to the driver through
``OPTIONS``. Plain Python with no Django import, so docker/entrypoint.sh can use it before
Django is configured. The same module lives in the subscription and onboarding APIs.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, unquote, urlsplit

DEFAULT_DATABASE_URL = "postgresql://postgres@localhost:5432/postgres"
DEFAULT_SCHEMA = "pettycashv3"
SCHEMES = {"postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"}


def database_url() -> str:
    """``DATABASE_URL`` from the environment, or the local default when unset/blank."""
    return os.environ.get("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL


def parse_database_url(url: str) -> tuple[dict, str]:
    """Return ``(db_dict, schema)`` for a postgres URL; raises ValueError on anything else."""
    parts = urlsplit(url)
    if parts.scheme not in SCHEMES:
        raise ValueError(f"DATABASE_URL scheme must be one of {sorted(SCHEMES)}, got {parts.scheme!r}")
    options = dict(parse_qsl(parts.query, keep_blank_values=True))
    schema = options.pop("schema", "") or DEFAULT_SCHEMA
    options["options"] = f"-c search_path={schema},public"
    db = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "OPTIONS": options,
    }
    return db, schema
