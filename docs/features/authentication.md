# Authentication and permissions — billing-backend's half

This service **verifies**; it never signs anyone in. Minty (Flask) mints the token a
person arrives with; the system-wide picture — the sign-in paths, sessions, the hand-off
tokens, the Xero OAuth tokens — is `Minty/docs/features/authentication.md`. This page is
what happens once a request reaches `/api/*` here.

## The bearer token

Every endpoint under `/api/` (Django Ninja, `config/urls.py`) is guarded by
`core/auth.BearerAuth`: `Authorization: Bearer <jwt>`, HS256 over the **shared
`SECRET_KEY`** (the same value as Minty's — a mismatch 401s everything and is logged with
a hash prefix of the key, never the key). The claims Minty puts in
(`_generate_module_token`): `user_id`, `entity_id`, `xero_org_id`, `role`, `system_role`,
`module`, `sid`, `billing_enabled`, `petty_cash_enabled`, `iat`, `exp` (30 minutes).

For each request the class:
1. decodes the token and loads the `user` row (an unknown user is a 401);
2. resolves the **entity**: the `X-Entity-Id` header first, else the token's `entity_id`;
   with neither, the request is *unscoped* — authenticated as a person with no role
   (`_attach_unscoped`), which only the `/api/auth/*` and `/api/profile/*` endpoints
   accept (`SelfBearerAuth`, `require_entity_role = False`);
3. reads the person's **role on that entity from the database** (`user_entity.role`) —
   never from the claim — and attaches `request.auth_user`, `request.entity_id`,
   `request.entity_role`, `request.is_entity_member`, `request.is_system_superuser`,
   `request.is_super_admin`. No row on the entity → 401 for every business endpoint.

A system superuser (`system_role = superadmin`) with no row on the entity gets a virtual
`super_admin` role to **read**; every write endpoint calls
`check_not_system_superuser(request, "…")` first and refuses them with a clean 403
(`core/permissions.py`; `bills/tests/test_superuser_cross_entity.py`).

## Session endpoints (`/api/auth/*`, `core/api_session.py`)

| Endpoint | What |
|---|---|
| `GET /session` | the role and entity of the current token, from the DB |
| `POST /token/refresh` | re-mints the billing JWT (8 hours, `BILLING_TOKEN_HOURS`) from a still-valid one — the frontend's cookie lives 8 hours too |
| `GET /me` | the person |
| `GET /entitlements` | `petty_cash_enabled` / `billing_enabled` read **live** from `entity_function_map` (`core/entitlements.py`) — the JWT's claims are hints only |
| `GET /xero-status` | whether the entity's Xero token bearer still holds a usable bundle |
| `GET /entity-currency` | the entity's ISO currency code |
| `POST /logout` | clears the presence stamps Minty keeps (`signed_in_at`, `last_seen_at`), so leaving from here counts as leaving |

## The permission matrix (`core/permissions.py`)

Roles come from `user_entity.role`: `cashier`, `shop_manager`, `accountant`, `admin`,
`super_admin` (`shared_models/enums.EntityRole`). "Elevated" = accountant and up.

| Action | Allowed |
|---|---|
| create a bill; edit or delete a Draft/Submitted one | every bill role |
| edit or delete a Paid / Partially-paid bill | elevated |
| record, change or delete a payment (mark paid) | elevated |
| publish / republish to Xero | elevated |
| return, un-return, void | elevated |
| edit bill settings (account codes, contacts, currencies) | elevated |

The checks are `check_create_bill`, `check_edit_bill(role, status)`, `check_delete_bill`,
`check_mark_paid`, `check_publish_xero`, `check_return_bill`, `check_edit_bill_settings`,
plus `check_bill_mutable(status)` (a `void` bill accepts nothing). `PermissionDeniedError`
→ 403 with the message the check carries (`core/exceptions.py`).

## Xero tokens

This service **never refreshes** a Xero token. `bills/services/xero_token_service.py`
uses the stored bundle of the entity's connector (`entities.connected_by_user_id`, else
the caller's own) while it is unexpired, and otherwise asks Minty:
`POST {XERO_TOKEN_SERVICE_URL}` (default `{FLASK_APP_URL}/api/internal/xero/token`) with a
60-second assertion JWT (`scope: xero-access-token`, the entity in the signed claims). A
409 from Minty means "reconnect" and surfaces as *Your Xero connection has expired*.
`XERO_CLIENT_ID` / `XERO_CLIENT_SECRET` exist in settings but must stay empty here —
populating them would make this a second refresher and brick the connection.

## Configuration

`SECRET_KEY` (shared), `FLASK_APP_URL` / `XERO_TOKEN_SERVICE_URL` / `XERO_TOKEN_SERVICE_TIMEOUT`,
`FRONTEND_APP_URL` (CORS), `MINTY_DB_SCHEMA` → `DB_SCHEMA` (the `search_path`).

## Tests

`bills/tests/test_bill_permissions.py`, `test_superuser_cross_entity.py`,
`test_token_refresh.py`, `test_billing_token_lifetime.py`, `test_logout.py`,
`test_xero_token_service.py`, `test_deactivate_account.py`; the frontend's
`e2e/01_handoff.spec.ts` proves the database entitlement, not the claim, decides what
shows.
