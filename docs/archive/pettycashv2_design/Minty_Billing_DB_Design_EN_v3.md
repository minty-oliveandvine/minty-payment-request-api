Minty Billing DB Design Document

Reviewed English-only edition with billing settings, Xero account mapping, and currency mapping

1. Overview

| Document purpose | Provide a table-by-table database design reference for Minty Billing covering billing, payments, attachments, feature settings, billing settings, currency settings, and Xero integration. |
| --- | --- |
| Scope | bill, bill_line_item, payment, attachment family, entity_function family, entity_bill_account_xero, currency_info, entity_bill_currency, and the Xero sync family |
| Baseline policy | All primary keys use UUID v4 strings (VARCHAR(36)). The design baseline assumes PostgreSQL 15. |

2. Key Relationships

| Relationship | Meaning |
| --- | --- |
| bill 1 : N bill_line_item | Bill header to line items |
| bill 1 : N payment | Supports multiple payments |
| bill 1 : N bill_attachment | Bill-to-file mapping |
| payment 1 : N payment_attachment | Payment-to-file mapping |
| entity 1 : N entity_function_map | Feature enablement mapping |
| entity 1 : N entity_bill_account_xero | Bill account code settings per entity |
| entity 1 : N entity_bill_currency | Allowed billing currencies per entity |
| currency_info 1 : N entity_bill_currency | Currency master reference |
| bill 1 : N xero_bill_sync | Xero sync attempt history |
| xero_bill_sync 1 : N xero_bill_sync_line | Line snapshots at sync time |
| xero_bill_sync 1 : 1 xero_bill_sync_payload | Raw request/response payload |
| xero_bill_sync 1 : N xero_bill_response_line | Normalized response lines |

3. Table Catalog

| Table | Business name | Core role |
| --- | --- | --- |
| bill | Bill header | Stores bill header information including status, total amount, invoice/due dates, Xero contact mapping, and working currency. |
| bill_line_item | Bill line item | Stores bill detail rows including quantity, unit amount, account code, and tax type. |
| payment | Payment history | Stores one or more payment records against a bill, including method and external reference. |
| attachment | Attachment metadata | Common file metadata store for uploaded documents. |
| bill_attachment | Bill attachment mapping | Maps attachments to bills and stores the business role of each file. |
| payment_attachment | Payment attachment mapping | Maps attachments to payments and stores the role of each payment file. |
| entity_function | Feature master | Master list of features that entities may enable. |
| entity_function_map | Entity-feature mapping | Stores which features are enabled for a given entity and the related settings. |
| entity_bill_account_xero | Entity Xero bill account setting | Stores the Xero account codes available to an entity in the bill UI. |
| currency_info | Currency master | Master list of currencies supported by the system. |
| entity_bill_currency | Entity billing currency mapping | Maps entities to the currencies they may use in billing and identifies the default currency. |
| xero_bill_sync | Xero bill sync log | Represents one outbound or inbound synchronization attempt for a bill with Xero. |
| xero_bill_sync_line | Xero sync line snapshot | Stores the request-time line item snapshot plus returned line identifiers. |
| xero_bill_sync_payload | Xero raw request-response payload | Stores the raw request/response JSON and headers for Xero API calls. |
| xero_bill_response_line | Xero response line detail | Normalizes returned Xero LineItems into a dedicated detail table. |

4. bill

| Business name | Bill header |
| --- | --- |
| Description | Stores bill header information including status, total amount, invoice/due dates, Xero contact mapping, and working currency. |
| Primary relationship | entity -> bill, bill -> bill_line_item, bill -> payment, bill -> xero_bill_sync |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK, UUID v4 |
| entity_id | VARCHAR(36) | Entity identifier |
| contact | VARCHAR(100) | Display contact name |
| xero_contact_id | VARCHAR(36) | Mapped Xero ContactID |
| status | VARCHAR(30) | draft / submitted / authorised / partially_paid / paid / voided / cancelled / sync_failed |
| amount | NUMERIC(12,2) | Header total |
| description | TEXT | Bill summary description |
| invoice_date | DATE | Invoice date |
| due_date | DATE | Due date |
| reference | VARCHAR(255) | Business reference |
| currency_code | VARCHAR(10) | Selected billing currency |
| published | VARCHAR(30) | not_published / published / failed |
| uploaded_by | VARCHAR(36) | Uploader |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• bill.amount should match the sum of bill_line_item.line_amount.

• bill.currency_code should be limited to currencies enabled in entity_bill_currency.

• Payment completion updates the bill toward partially_paid or paid based on summed payment amounts.

5. bill_line_item

| Business name | Bill line item |
| --- | --- |
| Description | Stores bill detail rows including quantity, unit amount, account code, and tax type. |
| Primary relationship | bill -> bill_line_item, bill_line_item -> xero_bill_sync_line |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| bill_id | VARCHAR(36) | FK -> bill.id |
| description | TEXT | Line description |
| quantity | NUMERIC(12,4) | Quantity |
| unit_amount | NUMERIC(12,2) | Unit amount |
| line_amount | NUMERIC(12,2) | Line total |
| account_code | VARCHAR(20) | Selected account code |
| account_name | VARCHAR(150) | Display name of account |
| tax_type | VARCHAR(30) | Tax type |
| sort_order | INTEGER | Display order |
| note | TEXT | Optional note |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• The account code dropdown shown in the bill UI should be sourced from entity_bill_account_xero.

• line_amount is generally derived as quantity × unit_amount.

6. payment

| Business name | Payment history |
| --- | --- |
| Description | Stores one or more payment records against a bill, including method and external reference. |
| Primary relationship | bill -> payment, payment -> payment_attachment |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| bill_id | VARCHAR(36) | FK -> bill.id |
| payment_date | DATE | Payment date |
| amount | NUMERIC(12,2) | Payment amount |
| currency_code | VARCHAR(10) | Payment currency |
| payment_method | VARCHAR(50) | bank_transfer etc. |
| payment_status | VARCHAR(30) | pending / completed / failed / cancelled / refunded |
| reference_no | VARCHAR(100) | Bank or gateway reference |
| note | TEXT | Note |
| xero_payment_id | VARCHAR(36) | Optional Xero payment id |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• The effective paid date belongs to payment rather than bill.

• Bank slips are attached through payment_attachment.

7. attachment

| Business name | Attachment metadata |
| --- | --- |
| Description | Common file metadata store for uploaded documents. |
| Primary relationship | attachment -> bill_attachment, attachment -> payment_attachment |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| original_name | VARCHAR(255) | Original file name |
| stored_name | VARCHAR(255) | Stored file name |
| file_path | TEXT | Storage path |
| mime_type | VARCHAR(100) | MIME type |
| file_size | BIGINT | File size |
| file_extension | VARCHAR(20) | Extension |
| storage_provider | VARCHAR(50) | local / s3 / gcs / azure |
| checksum_sha256 | VARCHAR(128) | Checksum |
| uploaded_by | VARCHAR(36) | Uploader |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |
| is_deleted | BOOLEAN | Soft delete flag |
| deleted_at | TIMESTAMPTZ | Deleted timestamp |

Operational Notes

• A single attachment repository is used, while business ownership is represented through mapping tables.

8. bill_attachment

| Business name | Bill attachment mapping |
| --- | --- |
| Description | Maps attachments to bills and stores the business role of each file. |
| Primary relationship | bill <-> attachment mapping |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| bill_id | VARCHAR(36) | FK -> bill.id |
| attachment_id | VARCHAR(36) | FK -> attachment.id |
| attachment_role | VARCHAR(50) | invoice / supporting_document / receipt / approval_document / other |
| sort_order | INTEGER | Display order |
| note | TEXT | Optional note |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• A single bill can have multiple invoice or supporting files.

9. payment_attachment

| Business name | Payment attachment mapping |
| --- | --- |
| Description | Maps attachments to payments and stores the role of each payment file. |
| Primary relationship | payment <-> attachment mapping |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| payment_id | VARCHAR(36) | FK -> payment.id |
| attachment_id | VARCHAR(36) | FK -> attachment.id |
| attachment_role | VARCHAR(50) | bank_slip / remittance_proof / payment_receipt / other |
| sort_order | INTEGER | Display order |
| note | TEXT | Optional note |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• A payment can hold multiple bank slip or remittance proof files.

10. entity_function

| Business name | Feature master |
| --- | --- |
| Description | Master list of features that entities may enable. |
| Primary relationship | entity_function -> entity_function_map |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| function_code | VARCHAR(100) | Unique code |
| function_name | VARCHAR(150) | Display name |
| description | TEXT | Description |
| is_active | BOOLEAN | Active flag |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• Examples: BILLING, XERO_SYNC, PETTY_CASH, ATTENDANCE

11. entity_function_map

| Business name | Entity-feature mapping |
| --- | --- |
| Description | Stores which features are enabled for a given entity and the related settings. |
| Primary relationship | entity <-> entity_function mapping |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| entity_id | VARCHAR(36) | Entity identifier |
| entity_function_id | VARCHAR(36) | FK -> entity_function.id |
| is_enabled | BOOLEAN | Enabled flag |
| enabled_at | TIMESTAMPTZ | Enabled timestamp |
| disabled_at | TIMESTAMPTZ | Disabled timestamp |
| settings_json | JSONB | Feature settings |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• This table can store both module enablement and per-entity settings.

12. entity_bill_account_xero

| Business name | Entity Xero bill account setting |
| --- | --- |
| Description | Stores the Xero account codes available to an entity in the bill UI. |
| Primary relationship | entity -> entity_bill_account_xero, source for bill_line_item.account_code |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| entity_id | VARCHAR(36) | Entity identifier |
| account_code | VARCHAR(20) | Xero chart of account code |
| account_name | VARCHAR(150) | Display name |
| account_type | VARCHAR(50) | Expense / COGS etc. |
| is_default | BOOLEAN | Default account flag |
| is_active | BOOLEAN | Active flag |
| xero_account_id | VARCHAR(36) | Optional Xero account id |
| sort_order | INTEGER | Display order |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• This table drives the account code options shown for bill_line_item.account_code.

• Each entity may maintain its own enabled code set.

13. currency_info

| Business name | Currency master |
| --- | --- |
| Description | Master list of currencies supported by the system. |
| Primary relationship | currency_info -> entity_bill_currency |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| currency_code | VARCHAR(10) | ISO code, e.g. HKD |
| currency_name | VARCHAR(100) | Display name |
| symbol | VARCHAR(10) | Currency symbol |
| decimal_places | INTEGER | Decimal precision |
| is_active | BOOLEAN | Active flag |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• Examples: HKD, USD, KRW, JPY

14. entity_bill_currency

| Business name | Entity billing currency mapping |
| --- | --- |
| Description | Maps entities to the currencies they may use in billing and identifies the default currency. |
| Primary relationship | entity <-> currency_info mapping, source for bill.currency_code |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| entity_id | VARCHAR(36) | Entity identifier |
| currency_info_id | VARCHAR(36) | FK -> currency_info.id |
| is_default | BOOLEAN | Default billing currency |
| is_enabled | BOOLEAN | Enabled flag |
| sort_order | INTEGER | Display order |
| created_by | VARCHAR(36) | Creator |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• The bill currency dropdown should be sourced from entity_bill_currency joined to currency_info.

15. xero_bill_sync

| Business name | Xero bill sync log |
| --- | --- |
| Description | Represents one outbound or inbound synchronization attempt for a bill with Xero. |
| Primary relationship | bill -> xero_bill_sync -> payload / lines / response lines |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| bill_id | VARCHAR(36) | FK -> bill.id |
| sync_direction | VARCHAR(20) | outbound / inbound |
| sync_type | VARCHAR(30) | create_invoice / update_invoice / void_invoice / fetch_invoice |
| sync_status | VARCHAR(30) | pending / success / failed / partial |
| request_type | VARCHAR(20) | ACCPAY |
| request_status | VARCHAR(30) | AUTHORISED etc. |
| request_contact_id | VARCHAR(36) | Xero ContactID |
| request_invoice_number | VARCHAR(100) | Invoice number sent to Xero |
| request_reference | VARCHAR(255) | Reference |
| request_invoice_date | DATE | Invoice date |
| request_due_date | DATE | Due date |
| response_invoice_id | VARCHAR(36) | Returned Xero InvoiceID |
| response_invoice_number | VARCHAR(100) | Returned invoice number |
| response_status | VARCHAR(30) | Returned invoice status |
| response_amount_due | NUMERIC(12,2) | Amount due |
| response_amount_paid | NUMERIC(12,2) | Amount paid |
| response_total | NUMERIC(12,2) | Invoice total |
| response_currency_code | VARCHAR(10) | Currency |
| xero_response_id | VARCHAR(36) | Top-level response Id |
| xero_provider_name | VARCHAR(100) | Provider name |
| xero_datetime_utc | VARCHAR(100) | Response UTC string |
| http_status_code | INTEGER | HTTP status |
| idempotency_key | VARCHAR(100) | Idempotency key |
| retry_count | INTEGER | Retry count |
| last_retry_at | TIMESTAMPTZ | Last retry timestamp |
| has_errors | BOOLEAN | Error flag |
| error_message | TEXT | Error message |
| requested_by | VARCHAR(36) | Requested by |
| requested_at | TIMESTAMPTZ | Request timestamp |
| responded_at | TIMESTAMPTZ | Response timestamp |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• A bill may be sent to Xero multiple times, so a 1:N sync history is required.

16. xero_bill_sync_line

| Business name | Xero sync line snapshot |
| --- | --- |
| Description | Stores the request-time line item snapshot plus returned line identifiers. |
| Primary relationship | xero_bill_sync -> xero_bill_sync_line, optional back-link to bill_line_item |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| xero_bill_sync_id | VARCHAR(36) | FK -> xero_bill_sync.id |
| bill_line_item_id | VARCHAR(36) | FK -> bill_line_item.id, nullable |
| description | TEXT | Description |
| quantity | NUMERIC(12,4) | Quantity |
| unit_amount | NUMERIC(12,2) | Unit amount |
| line_amount | NUMERIC(12,2) | Line amount |
| account_code | VARCHAR(20) | Account code |
| tax_type | VARCHAR(30) | Tax type |
| sort_order | INTEGER | Display order |
| response_line_item_id | VARCHAR(36) | Returned Xero line item id |
| response_account_id | VARCHAR(36) | Returned Xero account id |
| response_tax_amount | NUMERIC(12,2) | Returned tax amount |
| created_at | TIMESTAMPTZ | Created timestamp |

Operational Notes

• This preserves the exact line values that were submitted at sync time.

17. xero_bill_sync_payload

| Business name | Xero raw request-response payload |
| --- | --- |
| Description | Stores the raw request/response JSON and headers for Xero API calls. |
| Primary relationship | one-to-one with xero_bill_sync |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| xero_bill_sync_id | VARCHAR(36) | FK -> xero_bill_sync.id, unique |
| request_json | JSONB | Raw request JSON |
| response_json | JSONB | Raw response JSON |
| request_headers | JSONB | Request headers |
| response_headers | JSONB | Response headers |
| created_at | TIMESTAMPTZ | Created timestamp |
| updated_at | TIMESTAMPTZ | Updated timestamp |

Operational Notes

• Keeping the raw JSON is important for debugging and auditability.

18. xero_bill_response_line

| Business name | Xero response line detail |
| --- | --- |
| Description | Normalizes returned Xero LineItems into a dedicated detail table. |
| Primary relationship | xero_bill_sync -> xero_bill_response_line |

Column Definition

| Column | Type | Note |
| --- | --- | --- |
| id | VARCHAR(36) | PK |
| xero_bill_sync_id | VARCHAR(36) | FK -> xero_bill_sync.id |
| xero_line_item_id | VARCHAR(36) | Returned line item id |
| description | TEXT | Description |
| quantity | NUMERIC(12,4) | Quantity |
| unit_amount | NUMERIC(12,2) | Unit amount |
| line_amount | NUMERIC(12,2) | Line amount |
| tax_type | VARCHAR(30) | Tax type |
| tax_amount | NUMERIC(12,2) | Tax amount |
| account_code | VARCHAR(20) | Account code |
| account_id | VARCHAR(36) | Account id |
| validation_errors | JSONB | Validation errors |
| created_at | TIMESTAMPTZ | Created timestamp |

Operational Notes

• Useful for reconciliation and validation of returned line items.

19. Operating Rules

• The bill account code dropdown must come from entity_bill_account_xero and should only show active rows for the selected entity.

• The bill currency dropdown must come from entity_bill_currency joined to currency_info and should default to the row marked is_default.

• bill.amount should equal the sum of bill_line_item.line_amount values.

• The sum of completed payment.amount values determines whether a bill is unpaid, partially_paid, or paid.

• Each Xero sync attempt must retain both normalized fields and raw request/response JSON.
