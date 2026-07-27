# Load-bearing patch targets (survey before consolidation)

Any name below is patched by module path in tests. If its *usage* moves into a
different module, the patch stops intercepting and the test breaks silently or loudly.
**These names must keep being looked up in the module where they currently live.**

## monkeypatch.setattr — 1 occurrence

| Target | Patched from |
|---|---|
| `bills.services.xero_token_service.requests.post` | `bills/tests/conftest.py:27` (autouse "real HTTP blocked" guard) |

## patch("string") — direct

| Target | Patched from |
|---|---|
| `bills.services.attachment_service._get_s3_client` | test_attachment.py, test_payment_attachment_upload.py (8x) |
| `bills.services.xero_token_service.requests.post` | test_token_refresh.py, test_xero_token_service.py (3x) |
| `bills.services.contact_service.requests.get` | test_contact_no_duplicates.py etc. (3x) |
| `bills.api.publish_bill_to_xero` | test_bill_* (2x) |
| `bills.api.resolve_xero_access_token_for_entity` | test_bill_* (2x) |
| `bills.services.xero_publish_service.requests.post` | (1x) |

## patch(CONSTANT) — resolved via module/class-level path constants

All of these resolve into **`bills.services.xero_publish_service`** unless noted:

| Target | Patched from |
|---|---|
| `xero_publish_service._delete_xero_file` | test_xero_publish.py, test_xero_publish_bankslip.py |
| `xero_publish_service._download_from_s3` | test_xero_publish_bankslip.py |
| `xero_publish_service._get_s3_client` | test_xero_publish.py, test_xero_publish_bankslip.py |
| `xero_publish_service._get_xero_files_for_invoice` | test_xero_publish.py, test_xero_publish_bankslip.py |
| `xero_publish_service._upload_and_associate_bill_attachment` | test_xero_publish.py |
| `xero_publish_service._upload_bill_attachments_to_xero` | test_xero_publish.py, test_xero_publish_contact_fallback.py |
| `xero_publish_service._upload_existing_bankslips_to_xero` | test_xero_publish.py, test_xero_publish_contact_fallback.py |
| `xero_publish_service.requests.post` / `.put` | test_xero_publish.py, test_xero_publish_contact_fallback.py |
| `xero_publish_service.resolve_xero_access_token_for_entity` | test_xero_publish.py |
| `bills.services.contact_service.XeroContactSync.objects` | test_contact_dedup.py |

(`config.wsgi.application` and `django.db.models.BigAutoField` are Django settings
strings, not test patches — listed only so the inventory is complete.)

## Consequences for this cleanse

1. **`xero_publish_service.py` (1180 LOC) is the most patch-constrained file in the repo.**
   Its leading-underscore helpers look private/dead but are ALL patched by name. Do not
   move, rename, or inline them. Extracting shared logic out of this module is only safe
   if the *original names stay in place* as the thing tests patch.
2. **`resolve_xero_access_token_for_entity` and `publish_bill_to_xero` are patched at BOTH
   `bills.api` and `bills.services.xero_publish_service`.** They're imported into `bills.api`,
   so the two patch sites are distinct objects. Changing `bills/api.py` from
   `from ... import publish_bill_to_xero` to `from ... import xero_publish_service` +
   `xero_publish_service.publish_bill_to_xero(...)` would break the `bills.api.*` patches.
   Import style in `bills/api.py` is load-bearing.
3. **`requests` must stay imported as a module** (`import requests` + `requests.post(...)`)
   in xero_token_service, contact_service, and xero_publish_service. Switching to
   `from requests import post` breaks the patches, including the conftest HTTP guard.
4. `_get_s3_client` is defined **once**, in `attachment_service.py:99`, and imported into
   `xero_publish_service.py:26`. It is NOT duplicated code. But it is patched under two
   different module paths (`attachment_service._get_s3_client` and
   `xero_publish_service._get_s3_client`), which bind to different objects. The
   `from ... import _get_s3_client` line in xero_publish_service is therefore load-bearing:
   rewriting it to `attachment_service._get_s3_client(...)` would make the
   `xero_publish_service._get_s3_client` patches no-ops and silently let real boto3 calls
   through in tests. Leave it alone.
