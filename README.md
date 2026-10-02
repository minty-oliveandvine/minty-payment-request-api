# minty-payment-request-api

The payment-request (bills) API behind `minty-payment-request-web`: Django 5 + django-ninja, port
**8020**. It verifies the token Minty mints, keeps bills, payments and attachments, and
publishes bills to Xero as ACCPAY invoices. The API docs are served at `/api/docs`.

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env                 # then fill in SECRET_KEY (shared with Minty), DATABASE_URL, S3_URL
python manage.py runserver 8020
pytest                                # Postgres; needs MINTY_REPO for the schema harness (419 passed on 2026-10-01)
```

## Environment

All of it is in [`.env.example`](.env.example); `config/settings.py` reads it.

| Variable | Default | |
|---|---|---|
| `APP_ENV` | `production` | `development` turns on `DEBUG`. Outside development the placeholder `SECRET_KEY` refuses to boot. |
| `SECRET_KEY` | placeholder | Shared with Petty Cash (Minty): verifies its tokens and signs calls to its Xero token endpoint. |
| `ALLOWED_HOSTS` | `*` | Comma-separated. |
| `DATABASE_URL` | `postgresql://postgres@localhost:5432/postgres` | `postgresql://user:pass@host:5432/db?schema=pettycashv3[&sslmode=require]`. `?schema=` (default `pettycashv3`) becomes `search_path` / `settings.DB_SCHEMA`; other query params go to the driver. Parsed by `config/dburl.py`. In `.env`, `DB_SCHEMA=pettycashv3` + `?schema=${DB_SCHEMA}` keeps the name on its own line. |
| `S3_URL` | unset | `https://KEY:SECRET@s3.<region>.backblazeb2.com/<bucket>` (`?region=` overrides). Parsed by `config/s3url.py`. |
| `PETTY_CASH_URL` | `http://localhost:8010` | Flask app: chart/contact sync, and the Xero token service at `/api/internal/xero/token`. |
| `PAYMENT_REQUEST_WEB_URL` | `http://localhost:3020` | Landing redirect target; CORS. |
| `ONBOARDING_WEB_URL` | `http://localhost:3030` | CORS. |
| `CORS_ALLOWED_ORIGINS` | the two web URLs | Optional comma-separated override. |
| `LOG_LEVEL` | `INFO` | Optional. |
| `RUN_MIGRATIONS`, `DB_WAIT_SECONDS` | `true`, `180` | Optional; `docker/entrypoint.sh` only. The container listens on `PORT` (default 8020). |

There are deliberately no Xero OAuth credentials here: Xero refresh tokens are single-use and
the Flask app is the only service that refreshes them.

Tests run on SQLite by default; `MINTY_TEST_PG_URI` (may carry `?schema=`) switches to a
Postgres database built from Minty's schema file.

## Docs

- [`docs/features/README.md`](docs/features/README.md) — one page per feature: authentication
  and the permission matrix, bills, payments and bank slips, attachments, the Xero publish,
  entity configuration.
- [`docs/ERROR_COPY.md`](docs/ERROR_COPY.md) — the user-facing error standard shared across the
  Minty repos.
- [`docs/code_cleanse/CODE_CLEANSE_NOTES.md`](docs/code_cleanse/CODE_CLEANSE_NOTES.md) — the
  July cleanse log; [`docs/archive/`](docs/archive/) — the `pettycashv2`-era billing design.
