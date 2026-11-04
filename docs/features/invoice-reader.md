# Invoice reader - reading an invoice into the new-bill form

When an invoice is attached in minty-payment-request-web's new-bill form, a vision model reads it
and the form's empty fields are **filled as suggestions** the person checks (owner, 2026-11-04):
the supplier, the invoice and due dates, the amount, the description and the account. Nothing is
saved until Confirm or Save as Draft, a supplier is never created automatically, and the form
stays usable while the read runs (or fails). The bill number is not read: it is Minty's own
`MBI...` reference.

It is built the same way as Petty Cash's receipt reader (minty-petty-cash-api's
`docs/features/expense-ai.md`), on the same engine.

## Where it lives

- `POST /api/v1/bills/extract/` (`bills/api.py::extract_invoice_endpoint`) - multipart, one
  `invoice`. Who may ask: `check_create_bill` and not a system superuser outside their own
  company (403). The file: one PDF / JPEG / PNG of at most 10 MB, judged by its bytes
  (`invoice_reader.checked_invoice`; 422 `{"detail": sentence}`: "Attach the invoice to read
  it.", "Attach one invoice at a time.", "That file is empty. Attach the invoice again.",
  "Attach a file of 10 MB or less.", "Attach a PDF, JPEG or PNG file."). The attachment allowlist
  is wider (Word, Excel, HTML); the reader takes only what the model reads. Past those checks
  every outcome is 200 `{suggestions, reason}`, `suggestions` null on any failure.
- `bills/services/invoice_reader.py` - the invoice's half: the instruction, the reply's shape
  (`InvoiceSuggestion`), the lists sent with it and the check of the reply against them.
  - **Suppliers**: the company's synced contacts of its current Xero organisation
    (`contact_service.xero_org_scope`; rows with no org recorded count as legacy and are kept),
    from the database only - never a live Xero call per read.
  - **Accounts**: `entity_bill_account_xero` rows that are active, not deleted and of
    `BILL_SETTINGS_ACCOUNT_TYPES` - the form's dropdown - sent and matched by **code** (bills store
    codes; an account added by hand has no Xero id).
  - **Checks**: a supplier id or account code is accepted only if it was sent; the amount is a
    positive decimal up to 10,000,000 in the company currency's own places (JPY 0); a date that is
    not `YYYY-MM-DD` is dropped, and so is a due date before the invoice date; the description is
    capped at 200 characters and an unmatched supplier name (`detected_name`, for the form's
    "+ Add ... as a new supplier") at 100. The currency read off the invoice is handed on only so
    the form can note a mismatch: a bill always takes the company's currency.
- `minty_shared.ai` (minty-shared-py, extra `ai`) - the engine: the switch, the call and its one
  retry, the per-process rate limit, usage and cost, and the ONE log line (numbers only, never a
  value read off an invoice; logger `minty-shared`, into `logs/core.log`).
  `minty_shared.model_input` makes the small copy the model reads (an image capped at 2048 px and
  saved once as JPEG; a PDF's first page). The stored attachment is never changed.

## Configuration

| Variable | Meaning |
|---|---|
| `BILL_AI_ENABLED` | off unless on - the route answers `disabled`. Its own switch: the receipt reader's `EXPENSE_AI_ENABLED` does not turn it on |
| `BILL_AI_MODEL` (`gemini-3.5-flash`), `BILL_AI_THINKING_LEVEL` (`low`), `BILL_AI_MAX_OUTPUT_TOKENS` (4096), `BILL_AI_TIMEOUT_S` (25) | the call |
| `BILL_AI_RATE_USER_PER_MIN` (10), `BILL_AI_RATE_ENTITY_PER_HOUR` (200) | in-process rate limits per person and per company |
| `BILL_AI_CONF_HIGH` (0.85), `BILL_AI_CONF_MEDIUM` (0.60) | the confidence bands: low is never filled |
| `BILL_AI_MAX_FILE_MB` (10) | the model's size cap |
| `GEMINI_API_KEY`, `EXPENSE_AI_ALLOW_DIRECT_API`, `EXPENSE_AI_DIRECT_TIER`, `EXPENSE_AI_USE_VERTEX`, `EXPENSE_AI_LOCATION`, `GOOGLE_CLOUD_PROJECT` | the route to the model and the tier assertion - shared with the receipt reader (direct Gemini API, paid tier; no residency commitment, so invoices may be processed outside Singapore) |

## Tests

`bills/tests/test_invoice_reader.py` - the route (who may ask, the file refusals, the silent
reasons, its own switch), the context sent (only the form's accounts and the current org's
suppliers, in a stable order), the reply checks, and the one log line; the model is a stand-in.
The engine's own cases are minty-shared-py's `tests/test_ai.py`.
