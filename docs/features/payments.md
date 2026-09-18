# Payments and bank slips

Recording that a bill was paid — in full, in parts, by which method — and getting the
bank slip onto the Xero invoice. Code: `bills/api_payments.py`, `bills/services/payment_service.py`,
the `Payment` and `PaymentAttachment` models (`payment`, `payment_attachment`), and the
bank-slip half of `bills/services/xero_publish_service.py`.

## Recording a payment

`POST /api/bills/{bill_id}/payments` (elevated roles — `check_mark_paid`):
`payment_date`, `amount`, `payment_method` (free text: bank transfer, cheque, …),
`payment_status` (`pending` / `partial` / `completed` / `failed`), `reference_no`.
`PUT …/payments/{id}` and `DELETE …/payments/{id}` (elevated) edit or remove one;
`GET …/payments` lists the supplier's payments across all their bills;
`GET …/payments/{id}` one.

Rules:
- **No overpayment**: pending + completed payments may not exceed `bill.amount`
  (`_check_payment_within_bill_total`, a 422 that says so).
- The bill's status follows its **completed** payments: their sum ≥ `amount` → `paid`,
  above zero → `partially_paid`, otherwise back to `submitted`
  (`sync_bill_status_from_payments`, run after every create / update / delete, with a
  `status_changed` audit line). `get_amount_due` is what the detail page shows.
- Every payment action is audited (`payment_created` / `payment_updated` /
  `payment_deleted`, `marked_paid`).

## Bank slips

A payment carries its own attachments (`payment_attachment`, role `bank_slip`):
`POST /api/bills/{bill}/payments/{payment}/attachments` (multipart), `GET` list,
`DELETE …/attachments/{id}`, `…/download` (a 15-minute presigned URL) and
`…/preview/` (proxied through the service so the browser can show it inline without a
cross-origin block). Keys are `attachments/payments/<bill>/<payment>/…` in the shared B2 bucket (bill
attachments sit under `attachments/<entity>/<bill>/…`),
images downsized on the way in (`file_downsize.py`), 10 MB cap.

`POST /api/xero/upload-bankslip` (`bills/api_xero_actions.py`) pushes a payment's slips to
the Xero invoice as a **full replace** (`upload_bankslip_to_xero`): every FileId this
payment uploaded before — tracked on the row, plus anything on the invoice named
`*_BANKSLIP*` as defence in depth — is deleted from the Files API, then each current slip
is downloaded from S3, uploaded and associated, and its new FileId stored. So repeated
publishes never pile duplicate slips onto the invoice, and the pattern filter keeps it
from ever deleting the bill's own invoice attachments. `upload_bankslip_background` runs
the same thing in a thread when a publish triggers it.

## Tests

`bills/tests/test_payment_isolation.py`, `test_payment_attachment_upload.py`,
`test_xero_publish_bankslip.py`, `test_attachment_types.py`; the frontend's Record
Payment modal and bank-slip modal exercise these endpoints in the browser.
