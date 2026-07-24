#!/usr/bin/env sh
set -eu

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${POSTGRES_USER:-minty_ref_user}"
DB_NAME="${POSTGRES_DB:-minty_ref}"

# Wait for the database to accept connections before doing anything else.
echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
wait_seconds="${DB_WAIT_SECONDS:-60}"
attempt=0
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$wait_seconds" ]; then
    echo "Database not ready after ${wait_seconds} attempts. Giving up."
    exit 1
  fi
  sleep 1
done
echo "Database is ready."

# Billing shares the pettycashv2 schema with the Flask app (Module 1). Ensure it
# exists so Django migrations have a schema to write into on a fresh database.
PGPASSWORD="${POSTGRES_PASSWORD:-minty_ref_pass}" psql \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 \
  -c "CREATE SCHEMA IF NOT EXISTS pettycashv2;"

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running database migrations..."
  python manage.py migrate --noinput
fi

echo "Starting application command: $*"
exec "$@"
