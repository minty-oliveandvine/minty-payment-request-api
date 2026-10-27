# Attachments

The invoice a payment request is for, and any supporting document, live in the shared
Backblaze B2 bucket (S3 API; one `S3_URL`, parsed by `minty_shared.config.parse_s3_url` into the `S3_*`
settings in `config/settings.py`; **the API refuses to start without it** since 2026-10-05 - an
unset `S3_URL` used to start fine and answer 500 "Invalid bucket name" on every invoice and
bank-slip upload, which a `.env` still on the old `S3_BUCKET`/`S3_KEY`/... names did) with one
`attachment` row each and
a `bill_attachment` link (role `invoice` / `supporting_document` / `receipt`); a payment's
bank slips are `payment_attachment` rows ([payments.md](payments.md)). Code:
`bills/services/attachment_service.py`, `file_downsize.py`, the attachment routers in
`bills/api.py` and `bills/api_payments.py`.

## Endpoints (`/api/bills/{bill_id}/attachments`)

| Verb | What |
|---|---|
| `POST` (multipart, one or more files, `attachment_role`) | upload; refused on a `void` bill; 10 MB per file (`MAX_FILE_SIZE`); images are downsized before storage; an audit line `attachment_uploaded` |
| `GET` | the list, each with a presigned download URL |
| `DELETE …/{attachment_id}` | removes the object and the row (`attachment_deleted`) |
| `GET …/{attachment_id}/download` | a 15-minute presigned URL |
| `GET …/{attachment_id}/preview/` | the bytes streamed **through this service** — the browser's preview iframe cannot load a cross-origin B2 URL directly |

Keys: `attachments/<entity_id>/<bill_id>/<name>`; the stored name and MIME type describe
the bytes actually stored (a PNG may have been re-encoded). `storage_provider` is `s3`.

## What Confirm needs

The dialog requires at least one attachment and uploads it right after the bill is created,
so the API cannot require one at submit (it accepts a line item or an attachment); it is
**publishing** that refuses a bill with no undeleted attachment (`publish_bill_to_xero`,
owner 2026-10-27). If an upload fails after submit the dialog says so ("…sent, but X
didn't upload") and the request stays submitted without it. The API
tests cover the upload with the storage stubbed (`bills/tests/test_attachment.py`,
`test_attachment_types.py`, `test_payment_attachment_upload.py`). Against a real bucket
the browser does it in `minty-payment-request-web/e2e/04_xero_publish.spec.ts` (`E2E_XERO=1`).

## To Xero

On publish every bill attachment is pushed to the invoice through the Files API as a full
replace; the FileIds come back onto the rows (`xero_attachment_id`) so the next publish
can clean up ([xero-publish.md](xero-publish.md)).
