# billing-backend

The payment-request (bills) API behind `billing-frontend`: Django 5 + django-ninja, port
**8000**. It verifies the token Minty mints, keeps bills, payments and attachments, and
publishes bills to Xero as ACCPAY invoices. The API docs are served at `/api/docs`.

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python manage.py runserver 8000      # .env: SECRET_KEY shared with Minty, FLASK_APP_URL, S3_*, MINTY_DB_SCHEMA
pytest                                # Postgres; needs MINTY_REPO for the schema harness (443 passed on 2026-09-18)
```

- [`docs/features/README.md`](docs/features/README.md) — one page per feature: authentication
  and the permission matrix, bills, payments and bank slips, attachments, the Xero publish,
  entity configuration.
- [`docs/ERROR_COPY.md`](docs/ERROR_COPY.md) — the user-facing error standard shared across the
  Minty repos.
- [`docs/code_cleanse/CODE_CLEANSE_NOTES.md`](docs/code_cleanse/CODE_CLEANSE_NOTES.md) — the
  July cleanse log; [`docs/archive/`](docs/archive/) — the `pettycashv2`-era billing design.
