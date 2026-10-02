# Features — the payment-request API

`minty-payment-request-api` is the Django (Ninja) service behind the payment-request module. It
verifies the token Minty minted, keeps the bills, their payments and attachments, and
publishes bills to Xero; `minty-payment-request-web` is its browser. It reads and writes the same
Postgres schema as Minty (`?schema=` on `DATABASE_URL`, default `pettycashv3`) — the bills tables
are its own, everything else is `managed = False` and owned by Minty.

| Feature | Document |
|---|---|
| Verifying the bearer token, roles, the permission matrix, the session endpoints, Xero tokens | [authentication.md](authentication.md) — Minty's `docs/features/authentication.md` has the system-wide picture |
| Bills: draft → submitted → paid, return / void, references, listing, the audit trail | [payment-requests.md](payment-requests.md) |
| Payments, partial payments, the no-overpayment rule, bank slips to Xero | [payments.md](payments.md) |
| Attachments in B2, preview proxy, limits | [attachments.md](attachments.md) |
| Publishing an ACCPAY invoice, republish, org switch, lock dates | [xero-publish.md](xero-publish.md) |
| Modules, account codes, contacts, currencies, the sync triggers | [entity-configuration.md](entity-configuration.md) |
| User-facing error copy | [../ERROR_COPY.md](../ERROR_COPY.md) |

Running it: `manage.py runserver 8020` with `.env` (see `.env.example` and the README:
`APP_ENV`, `SECRET_KEY` shared with Minty, `DATABASE_URL`, `S3_URL`, `PETTY_CASH_URL`,
`PAYMENT_REQUEST_WEB_URL`, `ONBOARDING_WEB_URL`); the API docs are at `/api/docs`. Tests:
`pytest` (SQLite by default; with `MINTY_TEST_PG_URI` set, the harness builds Minty's
`docs/schema/01_schema_rebased.sql` — `conftest.py` needs `MINTY_REPO` pointing at a Minty
checkout). The archive of the `pettycashv2`-era design is in `../archive/`, the
cleanse log in `../code_cleanse/`.
