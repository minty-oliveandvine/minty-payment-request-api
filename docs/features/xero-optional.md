# Xero optional - bill lines by account, the publish snapshot, Minty-only companies

Since 2026-11-10 (minty-db v0.5's EXPAND; the design is Minty's
`docs/modernisation/xero_optional_database.md`).

**Bill lines name their account by id.** `bill_line.account_id` is kept in step by
`BillLine.save()`: the company's active account (`status = 'ACTIVE'`, `record_status = 'active'`) whose
Xero code - or, for an account the company made in Minty, its own code - is the line's
`account_code`. No such account, or no code, leaves it empty. The code text stays until CONTRACT,
when lines move to the id alone.

**The publish writes what it sent** (`xero_publish_service._snapshot`, after every successful create
or update): each line's `published_account_code` / `published_account_name` and the bill's
`published_contact_name`. A published bill shows those; no sync or later code change rewrites
them - only the next successful publish.

**A company keeping its books in Minty** (`setup_mode = 'minty'`, no Xero organisation;
`services/books.minty_on_its_own`):

| Where | What changes |
|---|---|
| `GET /entity-bill-contacts/` | Its suppliers are Minty's own rows (the database path, as for any company with no live Xero). One with no Xero id is named by its row id (`id` and `xero_contact_id` in the answer) |
| `POST /entity-bill-contacts/` | Kept in Minty (`xero_contact_sync` with no Xero id, `source = 'user'`), with no Xero call. A name it already has, ignoring case and spaces, is that supplier |
| A bill's supplier (`Bill.xero_contact_id`) | Takes a Xero id or, for a supplier with none, its row id; for such a company no Xero-keyed row is ever made |
| `GET /auth/xero-status` | Also answers `minty_only` (added 2026-11-10; the OpenAPI snapshot took it deliberately). minty-payment-request-web hides every Publish for it and shows its bill codes in Settings |
| `POST /bills/{id}/publish/` | 422 _This company keeps its books in Minty and isn't connected to Xero, so there's nowhere to publish to._ - before Xero is asked |

Its bill codes are its own expense accounts: onboarding's Accounting Setup writes them to
`entity_bill_account_xero` with no Xero id, as a hand-added code is.

Tests: `bills/tests/test_minty_on_its_own.py`.

**Loaded bills' suppliers (2026-11-10):** the v2->v3 load wrote Xero's ContactID into
`bill.contact_id`; minty-db's data patch `2026-11-10_bill_contact_ids.sql` relinks them (applied on
local `postgres`; Supabase waits for the owner), and Minty's load now does the same (`gen.py`).

**A disconnected company** keeps its Xero organisation (2026-11-10, minty-accounts-api): the token
lookup (`xero_token_service._resolve_token`) refuses it before any token is looked at - _This company
is disconnected from Xero. Reconnect Xero to publish._ - so a member's own Xero token never stands in
for the connection the company dropped.

## Connecting Xero later (Settings Phase 2, 2026-11-11)

A Minty company that signs in to Xero is matched first (minty-accounts-api `xero/services/matching.py`):
until Finish it keeps `setup_mode` `minty` with an organisation recorded, and publishing is refused
before any Xero call ("Finish matching your accounts to Xero first (Settings, under Integration), then

**Phase 3 of the matching (owner, 2026-11-12).** The same refusal holds every publish while
`entities.xero_matching` is `first`, `new_accounts` or `new_org`; a change of the matches (`change`)
doesn't hold it. Then a stale match is refused, one sentence an account, before anything is sent: an
account the bill uses with no Xero id, or one the current organisation's copies (`xero_account`) don't
hold - "Rent (469) isn't matched to your Xero organisation. Match it in Settings, under Integration,
then publish again." - or one Xero archived - "Rent (469) is archived in Xero. Restore it in Xero or
choose another account, then publish again." A company with no copies at all isn't held (older data).
Suppliers aren't held: the publish re-creates (Petty Cash) or re-publishes into the current
organisation (Payment Request) as before. Checked on production's data (local `postgres`, 2026-11-12):
4 companies have an unpublished record on an account Xero archived; no other publish changes.
publish."). Finish sets `entities.xero_publish_from` to that day (owner: only records from then on go
to Xero), so a payment request dated before it is refused ("This payment request is from before Xero was connected, so
it stays in Minty."). NULL - every company connected from the start - is no cut-off.

**Minty's books until Finish (2026-11-11).** `minty_on_its_own` is `setup_mode = 'minty'` alone: an
organisation recorded mid-matching no longer makes the company a Xero company here (it did, and
Petty Cash Settings drew empty Xero pickers while the company was wrongly marked connected).
Matching's Finish sets `setup_mode = 'xero'`.
