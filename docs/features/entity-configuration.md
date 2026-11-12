# Entity configuration — modules, account codes, contacts, currencies

The per-company reference data the payment module reads. Most of it is **written by
Minty** (the module switches, the synced chart and contacts); this service serves it and
edits the parts that belong to bills. Code: `bills/api_config.py`, `core/entitlements.py`,
`bills/services/contact_service.py`, `xero_sync.py`; models in `bills/models.py`
(`entity_function`, `entity_function_map`, `entity_bill_account_xero`, `currency_info`,
`entity_bill_currency`) — every table is `managed = False` here except the bills' own.

## Modules

`entity_function` is the catalogue (`PETTY_CASH`, `PAYMENT_REQUEST`) and
`entity_function_map` the per-company switch. `core/entitlements.py` mirrors Flask's
`_is_module_enabled`: an explicit map row wins, else the catalogue row's `is_active`, else
on (entities older than the gating table). `GET /api/auth/entitlements` is the live
answer the frontend uses to decide what it shows; the routers
`/api/entity-functions/*` and `/api/entity-function-maps/*` (`/functions` = the names
enabled for the entity) are **read-only**: their create / update / delete routes were removed
2026-11-03 (owner) - which modules a company has is the subscription service's to write, and
any admin of a company could switch a module on or off, or change the catalogue for everyone,
through them.

## Account codes (`/api/entity-bill-accounts/*`)

`entity_bill_account_xero` — the expense accounts the Add Payment dialog offers
(`account_code`, `account_name`, `account_type`, `is_default`, `is_active`, `sort_order`,
`xero_account_id`). Minty fills it from the Xero chart when a company connects and on every
sync accounts-api runs (`POST /api/internal/xero/sync`); this service lists it to cashier and up
(entity_base is refused, 2026-11-12) and lets elevated roles edit (`check_edit_bill_settings`;
`bills/tests/test_door_approval.py`). `GET /api/v1/entity-bill-accounts/rights` answers
`{can_edit, view_only}` from those same checks - minty-web's Payment Settings draws its controls
from it, never from a role read out of the token (owner, 2026-11-12). `GET /api/entities/` lists the
companies the caller may enter (all of them for a super admin, own only otherwise), never an
archived one (`archived_at` set; it replaced a dead `status != 'deleted'` filter, 2026-11-10).

`is_active` is the payment module's own tick (Payment Settings). `PUT /{id}` never touches
Petty Cash's `account_info.status` (Petty Cash's tick state; its publish refuses a non-ACTIVE
code), so the old "mirror into account_info" was removed on 2026-10-01 - it unticked codes in Petty
Cash. **The account's `account_module` PAYMENT_REQUEST row follows (2026-11-11):** `PUT /{id}` and
`DELETE /{id}` set its `is_active` (live and ticked) and `is_default` (one per company: the others
are cleared first), in the same transaction (`_mirror_module`), as onboarding's step 8 writes
both. The account is the `account_info` row with this Xero id, else (a Minty company's own) the
active one with this code; a code added by hand has none and changes no module row. **At least one ticked:** a PUT that would untick the entity's last live
(`is_deleted=False`) code of a `BILL_SETTINGS_ACCOUNT_TYPES` type answers **409** "Keep at least
one account code ticked." and writes nothing (the entity's rows are locked `FOR UPDATE` in id
order, so two parallel unticks cannot both pass). minty-payment-request-web sends ticks ON before OFF so a
swap never trips it.

**One code per Xero account (2026-11-03).** The table's partial unique index
`uq_ebax_entity_xero_account` (`(entity_id, xero_account_id) WHERE xero_account_id <> ''`, minty-db
v0.4) stops Minty's syncs writing a code twice. `POST /` with a `xero_account_id` the company already
has answers **409** "That Xero account is already in your list of payment account codes." - also when
a sync stores it between the look-up and the insert. Codes added by hand (`xero_account_id` `""`)
are not covered. `bills/tests/test_xero_unique_keys.py`.

## Contacts (`/api/entity-bill-contacts/*`)

`GET` returns the entity's suppliers from `xero_contact_sync` scoped to the entity's
**current** Xero organisation (`xero_org_scope`; no org → no scoping, so the cached list
still works when Xero is down), matched the way Minty matches; `POST` creates the contact
in Xero and upserts the cache row (`create_entity_bill_contact_in_xero`); a name over 100
characters is refused (422 "Keep the supplier's name to 100 characters or fewer.", because
`Bill.contact_name` holds 100) before Xero is called. Duplicates and
exact-match rules: `bills/tests/test_contact_*.py`. One cache row per Xero contact per company
(`uq_xcs_entity_contact`, 2026-11-03): when Minty's sync stored the contact between the look-up
and the insert, that row is the contact (`bills/tests/test_xero_unique_keys.py`).

**Never blocked behind Xero, never a duplicate (owner, 2026-11-12).** `POST` returns the
company's active supplier of that name when it has one (case and inner spaces ignored); with no
live connection (not connected, or still keeping its books in Minty) the supplier is the company's
own row, no Xero id; live, Xero's contact of that name is linked from the stored copies
(`xero_contact`) before one is made (`xero_contact_for`). `GET` lists those Minty-only suppliers
beside the live ones, and leaves archived and merged rows out. **Publish puts a Minty-only supplier
in Xero first** (`xero_publish_service`): it used to send the row's own id as the `ContactID`
(`bill.xero_contact_id` falls back to it); now Xero's contact of the name is linked, else made,
and a refusal names the supplier with Xero's reason, nothing published
(`bills/tests/test_xero_publish_contact_fallback.py`).

## Currencies

`currency_info` is the registry (ISO code, symbol, decimals) and `entity_bill_currency`
a company's allowed list; every amount the frontend shows uses the entity's currency
(`GET /api/auth/entity-currency` → `currency_code` and `decimal_places`, from
`currency_info`; `""` and 2 when none is set), never the bill's stored code. The frontend
sizes its amount inputs to `decimal_places` (JPY 0).

A bill's currency is set by the server from `entities.currency_id`
(`core/entity_currency.py`); the `currency_code` a client sends is ignored. An entity
with no currency cannot create a bill (create, draft and submit → 422 "Your company has no
currency set…"). An update fills a bill saved without one from the entity, and refuses a
submission only when neither has a currency, so older currency-less bills can still be
edited and voided.

A payment is in its bill's currency (the entity's when the bill has none; neither → 422),
set on create and never changed by an update; the client's `currency_code` is ignored here
too.

## Keeping the caches fresh

`xero_sync.py` asks accounts-api's one Xero sync (`POST /api/internal/xero/sync`, a 60-second
assertion scoped `xero-sync` naming the company; Phase 4 of the Flask retirement, 2026-11-06)
to refresh the accounts (`account_info`, which the Bill CoA list mirrors) and the contacts
together. The bill list (`GET /bills/`) and the Bill CoA settings (`include_inactive` or
`force_chart_sync`; `sync_chart=false` starts none - the hub reads a company's codes that way when
Xero isn't connected, owner 2026-11-12) start it on a background thread, debounced per company for 120 s
(`force_chart_sync` skips the wait); the screens render from the tables at once. Failures are
logged, never raised. It replaced three calls to Flask's `/api/entities/{id}/billing/sync-*`,
routes that checked only that the header began with `Bearer `.

## Tests

`bills/tests/test_entity_bill_accounts_list.py`, `test_entity_bill_accounts_update.py` (the
PUT leaves `account_info` alone, the 409, the function-map DELETE), `test_entity_bill_contacts_create.py`,
`test_contact_dedup.py`, `test_contact_list_exact_match.py`, `test_contact_list_parity.py`,
`test_contact_no_duplicates.py`, `test_account_restoration.py`, `test_depreciatn_account.py`,
`test_char_schema.py` and `test_schema_name.py` (the schema the models expect), `test_xero_sync.py`
(the sync call: its assertion, failures logged, the background debounce, the two screens that
start it).
