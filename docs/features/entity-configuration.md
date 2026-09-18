# Entity configuration — modules, account codes, contacts, currencies

The per-company reference data the payment module reads. Most of it is **written by
Minty** (the module switches, the synced chart and contacts); this service serves it and
edits the parts that belong to bills. Code: `bills/api_config.py`, `core/entitlements.py`,
`bills/services/contact_service.py`, `flask_billing_sync.py`; models in `bills/models.py`
(`entity_function`, `entity_function_map`, `entity_bill_account_xero`, `currency_info`,
`entity_bill_currency`) — every table is `managed = False` here except the bills' own.

## Modules

`entity_function` is the catalogue (`PETTY_CASH`, `PAYMENT_REQUEST`) and
`entity_function_map` the per-company switch. `core/entitlements.py` mirrors Flask's
`_is_module_enabled`: an explicit map row wins, else the catalogue row's `is_active`, else
on (entities older than the gating table). `GET /api/auth/entitlements` is the live
answer the frontend uses to decide what it shows; the routers
`/api/entity-functions/*` and `/api/entity-function-maps/*` (CRUD, `/functions` = the
names enabled for the entity) are the raw tables for tooling.

## Account codes (`/api/entity-bill-accounts/*`)

`entity_bill_account_xero` — the expense accounts the Add Payment dialog offers
(`account_code`, `account_name`, `account_type`, `is_default`, `is_active`, `sort_order`,
`xero_account_id`). Minty fills it from the Xero chart when a company connects (and on
`POST /api/entities/{id}/billing/sync-chart-accounts`); this service lists it and lets
elevated roles edit (`check_edit_bill_settings`). `GET /api/entities/` lists the
companies the caller may enter (all of them for a super admin, own only otherwise).

## Contacts (`/api/entity-bill-contacts/*`)

`GET` returns the entity's suppliers from `xero_contact_sync` scoped to the entity's
**current** Xero organisation (`xero_org_scope`; no org → no scoping, so the cached list
still works when Xero is down), matched the way Minty matches; `POST` creates the contact
in Xero and upserts the cache row (`create_entity_bill_contact_in_xero`). Duplicates and
exact-match rules: `bills/tests/test_contact_*.py`.

## Currencies

`currency_info` is the registry (ISO code, symbol, decimals) and `entity_bill_currency`
a company's allowed list; every amount the frontend shows uses the entity's currency
(`GET /api/auth/entity-currency`), never the bill's stored code.

## Keeping the caches fresh

`flask_billing_sync.py` asks Minty to refresh — `trigger_flask_bill_chart_sync`,
`trigger_chart_sync_if_changed` (compares Xero live against the DB and syncs both modules
if anything changed), `trigger_flask_contact_sync` — over the JWT-authenticated
`/api/entities/{id}/billing/sync-*` routes; failures are logged, never raised.

## Tests

`bills/tests/test_entity_bill_accounts_list.py`, `test_entity_bill_contacts_create.py`,
`test_contact_dedup.py`, `test_contact_list_exact_match.py`, `test_contact_list_parity.py`,
`test_contact_no_duplicates.py`, `test_account_restoration.py`, `test_depreciatn_account.py`,
`test_char_schema.py` and `test_schema_name.py` (the schema the models expect).
