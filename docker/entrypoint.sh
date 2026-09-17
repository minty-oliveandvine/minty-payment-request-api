#!/usr/bin/env sh
# Container entrypoint for the billing (Module 2) Django API.
#
# Django here is a *tenant* of the schema Flask owns: settings.py pins
# search_path to `pettycashv3`, and shared_models maps tables Flask's Alembic
# migrations create. So before migrating we wait for both the database and that
# schema — rather than creating the schema ourselves, which would race Alembic
# and let Django win the tables it is only supposed to read.
set -eu

python - <<'PY'
import os
import time

import psycopg2

host = os.environ.get("DB_HOST", "localhost")
port = os.environ.get("DB_PORT", "5432")
dbname = os.environ.get("POSTGRES_DB", "postgres")
user = os.environ.get("POSTGRES_USER", "postgres")
password = os.environ.get("POSTGRES_PASSWORD", "")

# Seconds, not attempts: the schema arrives only once Flask has finished its own
# migrations, which on a cold volume takes a while.
deadline = time.monotonic() + int(os.environ.get("DB_WAIT_SECONDS", "180"))
last_error = None

while time.monotonic() < deadline:
    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = 'pettycashv3'"
                )
                if cur.fetchone():
                    break
                last_error = "schema pettycashv3 does not exist yet"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a retry
        last_error = exc
    time.sleep(2)
else:
    raise RuntimeError(f"Database/schema not ready: {last_error}")
PY

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running Django migrations..."
  python manage.py migrate --noinput
fi

echo "Starting application command: $*"
exec "$@"
