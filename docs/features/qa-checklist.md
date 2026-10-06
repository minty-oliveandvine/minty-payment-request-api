# Manual QA checklist — payment-request API

A manual walkthrough checklist for `/api/*`, to run alongside the automated suites (the
`## Tests` section in each doc below). This is not a replacement for them — it exists for
exercising the service by hand (Postman, curl, or through `minty-payment-request-web`)
before a release, and for checking the sharp edges the docs call out explicitly. Use a
disposable entity connected to a Xero **Demo Company** — never a live customer org (see
"Xero publish is destructive by design" below).

## Auth (`BearerAuth`, [authentication.md](authentication.md))

Unlike onboarding's API, there is no "first call opens the door" here: every business
endpoint needs a real `user_entity` row before it answers anything.

- [ ] No `Authorization` header on a non-public endpoint → 401.
- [ ] Malformed token, or one signed with the wrong secret → 401.
- [ ] Expired token (30 min `exp`) → 401.
- [ ] Token for a `user_id` that doesn't exist → 401.
- [ ] A valid token for a user with **no** `user_entity` row on the resolved entity → 401 on
      every business endpoint (no "first call creates the role" exception anywhere in this
      service).
- [ ] `X-Entity-Id` header resolves the entity ahead of the token's own `entity_id` when
      both are present.
- [ ] No entity resolvable at all (no header, no token `entity_id`) → unscoped identity;
      only `/api/profile/*` and `/api/auth/entitlements` / `/api/auth/token/refresh` accept
      it — every other endpoint, including `/api/auth/xero-status`, refuses it.
- [ ] `GET /api/auth/xero-status` answers from the database, not a stale claim (regression
      check: since 2026-10-06 it reads the connector's live refresh-token row; it used to
      read the caller's own `user.refresh_token` after that column moved tables, swallow the
      error, and always answer `false`).
- [ ] A `system_role = superadmin` token with no row on the entity can still read (virtual
      `super_admin` role), but every write endpoint 403s via `check_not_system_superuser`
      with its own message.
- [ ] Changing a person's `user_entity.role` in the database takes effect on their very next
      request — no new token needed (the role is read from the DB each time, never from the
      JWT claim).

### Permission matrix (`core/permissions.py`)

- [ ] Every bill role (cashier, shop_manager, accountant, admin, super_admin) can create a
      bill and edit/delete one that is still Draft or Submitted.
- [ ] A non-elevated role (cashier, shop_manager) gets 403 on: editing/deleting a Paid or
      Partially-paid bill, recording/changing/deleting a payment, publish/republish to
      Xero, return/un-return/void, and editing bill settings (account codes, contacts,
      currencies). An elevated role (accountant, admin, super_admin) can do all of these.
- [ ] A `void` bill accepts no mutation at all, regardless of role.

## Session endpoints (`/api/auth/*`)

- [ ] `GET /session` returns the role and entity read from the DB for the current token.
- [ ] `POST /token/refresh` re-mints an 8-hour billing JWT from a still-valid one
      (`BILLING_TOKEN_HOURS`).
- [ ] `GET /entitlements` reflects `entity_function_map` live — toggle a module in the DB
      and confirm the answer changes without minting a new token.
- [ ] `GET /entity-currency` returns the entity's ISO currency code.
- [ ] `POST /logout` clears `signed_in_at` / `last_seen_at`.
- [ ] `GET /auth/me`, `PUT /profile/me`, `DELETE /profile/me` are gone (removed 2026-10-01)
      → 404. `GET /profile/me` still answers (`is_view_only`, `member_entity_ids`).

## Payment requests — bills ([payment-requests.md](payment-requests.md))

- [ ] `POST /api/bills/draft/` and `PUT /api/bills/{id}/draft` save with nothing filled in.
- [ ] Submitting (`POST /api/bills/submit/` or `POST /api/bills/`) without a contact, an
      amount above zero, an invoice date, a due date, or any attachment → 422 listing every
      missing field together, in the dialog's wording.
- [ ] `GET /api/bills/by-reference/{reference}` matches trimmed and case-insensitive; if a
      live bill and a void bill share a reference, the live one wins; otherwise the newest
      wins.
- [ ] Editing a Paid or Partially-paid bill needs an elevated role; the response carries the
      payment-synced status and `amount_due`.
- [ ] `POST /api/bills/{id}/return/` moves submitted → returned, returned → submitted, and
      returned → void, elevated roles only.
- [ ] `DELETE /api/bills/{id}`: a draft is hard-deleted (row gone); anything else becomes
      `void` (row stays).
- [ ] `GET /api/bills/suggested-reference/` produces `MBI` + three letters of the entity
      name + a Hong Kong-time timestamp, unique among the entity's non-voided bills; forcing
      a duplicate on submit is refused with a field error.
- [ ] Listing (`GET /api/bills/`) filters correctly on `status`, `search` (supplier,
      reference, description), `contact`, `amount_min`/`amount_max`, and a date range; the
      frontend's status tabs and its client-side Xero (published) filter both line up.
- [ ] `GET /api/bills/{id}/audit` shows a row for create, edit, submit, every status change,
      mark-paid, void, publish, and attachment upload/delete.

## Payments and bank slips ([payments.md](payments.md))

- [ ] `POST /api/bills/{id}/payments` requires an elevated role.
- [ ] Pending + completed payments may never sum above `bill.amount` → 422.
- [ ] Only **completed** payments drive status: sum ≥ amount → paid; above zero → partially
      paid; otherwise back to submitted. Each transition logs a `status_changed` audit line.
- [ ] `PUT`/`DELETE …/payments/{id}` are elevated-only and re-run the status sync.
- [ ] Bank-slip attachments (`…/payments/{payment}/attachments`) respect the 10 MB cap and
      downsize images on the way in.
- [ ] `POST /api/xero/upload-bankslip` is a full replace: republishing after adding another
      slip does not pile duplicates onto the Xero invoice (every FileId this payment
      uploaded before, plus anything on the invoice named `*_BANKSLIP*`, is deleted first),
      and it never touches the bill's own invoice attachments.

## Attachments ([attachments.md](attachments.md))

- [ ] Upload is refused on a `void` bill.
- [ ] A file over 10 MB is refused; images are downsized before storage.
- [ ] `DELETE …/{attachment_id}` removes both the B2 object and the row.
- [ ] `GET …/{attachment_id}/download` returns a 15-minute presigned URL.
- [ ] `GET …/{attachment_id}/preview/` streams through this service (the preview iframe
      loads it without a cross-origin error — a direct B2 URL would fail here).
- [ ] With `S3_URL` unset the service **refuses to start** (regression check, fixed
      2026-10-05: it used to boot fine and answer 500 "Invalid bucket name" on every upload).

## Publishing to Xero ([xero-publish.md](xero-publish.md))

- [ ] Publishing with no usable token (expired, not refreshable) returns a clear "reconnect
      required" response, not a generic error.
- [ ] Republishing into the **same** Xero org updates the existing invoice in place
      (`POST /Invoices/{id}`) — no duplicate invoice appears in Xero.
- [ ] After the entity reconnects to a **different** Xero org, the next publish resets the
      bill and creates a fresh invoice in the new org rather than patching the old org's sync
      row.
- [ ] A bill saved without `xero_contact_id` resolves the contact by name on publish, or
      creates it in Xero if missing.
- [ ] A bill dated on or before a period/year-end lock publishes as `DRAFT`; one dated after
      publishes `AUTHORISED`.
- [ ] Editing a bill's date into a newly-locked period, then republishing, voids the old
      `AUTHORISED` invoice and creates a new `DRAFT` one.
- [ ] `GET /api/bills/{id}/xero-syncs[/{sync_id}]` shows the stored request/response
      (`Authorization` header stripped) and `sync_status`.
- [ ] A publish that doesn't end in a successful sync row answers 422 carrying that sync's
      own `error_message` — never a false 200.
- [ ] The audit line reads "Published…" the first time and "Republished…" after.
- [ ] Every bill attachment is pushed to the invoice via the Files API as a full replace on
      each publish (old FileIds deleted first, same as the bank-slip behaviour above).

## Entity configuration ([entity-configuration.md](entity-configuration.md))

- [ ] `GET /api/auth/entitlements` resolution order holds: an explicit
      `entity_function_map` row wins, else the catalogue's `is_active`, else on (for
      entities older than the gating table).
- [ ] `PUT /api/entity-bill-accounts/{id}` writes only `entity_bill_account_xero` — Petty
      Cash's own `account_info.status` tick is untouched (regression check: the old mirror,
      removed 2026-10-01, used to untick Petty Cash's codes).
- [ ] Unticking the entity's **last** live code of a `BILL_SETTINGS_ACCOUNT_TYPES` type →
      409 "Keep at least one account code ticked." and nothing is written.
- [ ] Ticking a replacement code on before ticking the old one off (the sequence the
      frontend uses) never trips that 409.
- [ ] `GET /api/entities/` lists every entity for a super admin, only the caller's own
      otherwise.
- [ ] `GET /api/entity-bill-contacts/` is scoped to the entity's **current** Xero org; with
      no org linked the scoping is skipped and the cached list still answers.
- [ ] `POST /api/entity-bill-contacts/` creates the contact in Xero and upserts the cache
      row; a duplicate/near-duplicate name follows the documented exact-match rule.
- [ ] Every amount shown anywhere reads `GET /api/auth/entity-currency`'s code — never the
      bill's own stored `currency_code`.

## Xero publish is destructive by design — test against a Demo Company only

**Never run the publish, republish, or bank-slip checks above against a real customer's
connected Xero organisation.** Both the invoice attachments and a payment's bank slips are
pushed as a **full replace** on every publish: the service deletes the Files it uploaded
last time (plus, for bank slips, anything already on the invoice matching `*_BANKSLIP*`)
before re-uploading. Republishing after the entity reconnects to a *different* org silently
resets the bill's sync history and creates a brand-new invoice — the UI's own "Published"
badge won't tell you that happened; check `GET /api/bills/{id}/xero-syncs` for which org a
sync actually belongs to. And a bill whose date crosses into a newly-locked period gets its
AUTHORISED invoice **voided** and replaced on republish — an irreversible action against a
real company's books. Use a Xero Demo Company connected to a disposable entity
(`minty-payment-request-web/e2e/04_xero_publish.spec.ts` with `E2E_XERO=1` is the automated
version of the same walkthrough) and never a live org.

## Out of scope for this checklist

- **Xero's OAuth connect/reconnect flow itself, and refreshing a Xero token** — this
  service deliberately holds no Xero client credentials and never refreshes a token; that's
  Minty's (`Minty/docs/features/authentication.md` and `xero-integration.md`).
- **Minting the bearer token / the sign-in flow** — Minty mints it; this service only
  verifies.
- **Email delivery** — nothing in this service's documented surface sends mail.

## See also

- [authentication.md](authentication.md) — the auth and permission rules behind the first
  two sections.
- [payment-requests.md](payment-requests.md), [payments.md](payments.md),
  [attachments.md](attachments.md), [xero-publish.md](xero-publish.md),
  [entity-configuration.md](entity-configuration.md) — the endpoint references this
  checklist is derived from, each with its own `## Tests`.
- `minty-payment-request-web/e2e/02_bill_lifecycle.spec.ts` and
  `04_xero_publish.spec.ts` (`E2E_XERO=1`) — the automated browser suite covering much of
  the same ground.
