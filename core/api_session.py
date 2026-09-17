"""Session and token endpoints for the billing module."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from django.conf import settings
from ninja import Router, Schema

from core.auth import SelfBearerAuth

logger = logging.getLogger("minty-api")

session_router = Router(tags=["Session"])

BILLING_TOKEN_HOURS = 8


class BillingSessionOut(Schema):
    role: str
    entity_id: str


class TokenRefreshOut(Schema):
    token: str
    expires_in: int  # seconds


class XeroStatusOut(Schema):
    connected: bool


class EntitlementsOut(Schema):
    petty_cash_enabled: bool
    billing_enabled: bool


class CurrentUserOut(Schema):
    id: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    system_role: str


class LogoutOut(Schema):
    detail: str


class EntityCurrencyOut(Schema):
    # ISO 4217 code of the entity's selected currency ("" when unset).
    currency_code: str


@session_router.get(
    "/session",
    response={200: BillingSessionOut},
    summary="Current billing session (entity role from DB)",
)
def billing_session(request):
    """Return the caller's role for X-Entity-Id / token entity — same source as permission checks."""
    logger.info(
        "Billing session context user_id=%s entity_id=%s role=%s",
        getattr(request.auth_user, "id", None),
        getattr(request, "entity_id", None),
        getattr(request, "entity_role", None),
    )
    return BillingSessionOut(
        role=request.entity_role,
        entity_id=request.entity_id,
    )


@session_router.post(
    "/token/refresh",
    response={200: TokenRefreshOut},
    auth=None,  # auth handled explicitly below, so a missing token is our own 401
    summary="Refresh the billing JWT before it expires",
)
def token_refresh(request):
    """Exchange a valid (non-expired) billing JWT for a fresh 8-hour token.

    The caller must supply their current token in the Authorization header.
    Expired tokens are rejected — re-entry from Module 1 is required in that case.

    Uses ``SelfBearerAuth`` for the same reason the person-level reads do: the frontend
    refreshes pre-emptively before ANY call, so refusing a caller who holds no role on the
    entity in their token made every screen — including the ones that need no entity at
    all — report a timed-out session. The refreshed token is re-minted from
    ``request.entity_id``/``entity_role``, so an unscoped context yields an unscoped
    token: narrower than the one presented, never wider.
    """
    raw_token = (
        (request.headers.get("Authorization", "") or "").removeprefix("Bearer ").strip()
    )
    if not raw_token:
        from ninja.errors import HttpError

        raise HttpError(401, "Your session has expired. Sign in again to keep going.")

    auth = SelfBearerAuth()
    user = auth.authenticate(request, raw_token)
    if user is None:
        from ninja.errors import HttpError

        raise HttpError(401, "Your session has expired. Sign in again to keep going.")

    expires_in = BILLING_TOKEN_HOURS * 3600
    now = datetime.now(timezone.utc)

    # Re-resolve module entitlements from DB so the refreshed token reflects
    # any changes (e.g. an admin flipped BILL on after the initial handoff).
    # We never copy these forward from the old token because they could be
    # stale — entitlements are the kind of thing that must always be authoritative.
    from core.entitlements import get_module_claims

    module_claims = get_module_claims(str(request.entity_id or ""))

    new_token = jwt.encode(
        {
            "user_id": str(request.auth_user.id),
            "entity_id": str(request.entity_id or ""),
            "role": request.entity_role or "",
            "module": "billing",
            **module_claims,
            "exp": now + timedelta(seconds=expires_in),
            "iat": now,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    logger.info(
        "Token refreshed user_id=%s entity_id=%s",
        str(request.auth_user.id),
        request.entity_id,
    )
    return TokenRefreshOut(token=new_token, expires_in=expires_in)


@session_router.get(
    "/entity-currency",
    response={200: EntityCurrencyOut},
    summary="ISO currency code of the current entity (entities.currency_id)",
)
def entity_currency(request):
    """Resolve the JWT entity's selected currency to its ISO code.

    entities.currency_id is a uuid FK into pettycashv3.currency_info(id); the
    UI renders money amounts with the currency_code. Raw SQL because entities
    is Flask-managed and only mirrored read-only here.
    """
    from django.db import connection

    code = ""
    entity_id = str(getattr(request, "entity_id", "") or "")
    if entity_id:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT ci.currency_code FROM pettycashv3.entities e "
                "JOIN pettycashv3.currency_info ci ON ci.id = e.currency_id "
                "WHERE e.id = %s",
                [entity_id],
            )
            row = cur.fetchone()
            if row and row[0]:
                code = row[0]
    return EntityCurrencyOut(currency_code=code)


@session_router.get(
    "/me",
    auth=SelfBearerAuth(),
    response={200: CurrentUserOut},
    summary="Get current authenticated user data",
)
def current_user(request):
    """Return profile data for the currently authenticated user."""
    user = request.auth_user
    return CurrentUserOut(
        id=str(user.id),
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        system_role=user.system_role,
    )


@session_router.get(
    "/entitlements",
    auth=SelfBearerAuth(),
    response={200: EntitlementsOut},
    summary="Live module entitlements for the current entity (DB-fresh, not JWT)",
)
def entitlements(request):
    """Return the current module entitlements for the JWT's entity.

    Reads straight from ``entity_function_map`` (via core.entitlements) so the
    Module 2 frontend can refresh its module-visibility state without going
    back through Module 1 to re-mint a JWT. The Bearer auth gives us
    ``request.entity_id`` — we don't take it from a query string because that
    would let a caller probe entitlements for entities they don't belong to.
    """
    from core.entitlements import get_module_claims

    claims = get_module_claims(str(request.entity_id or ""))
    return EntitlementsOut(
        petty_cash_enabled=claims["petty_cash_enabled"],
        billing_enabled=claims["billing_enabled"],
    )


@session_router.get(
    "/xero-status",
    auth=SelfBearerAuth(),
    response={200: XeroStatusOut},
    summary="Check whether the current user's Xero credentials are still valid",
)
def xero_status(request):
    """Return whether the authenticated user has a live Xero refresh_token.

    The frontend uses this to display the Xero connection status indicator
    without making a live call to the Xero API.
    """
    try:
        from shared_models.models import User

        user = (
            User.objects.filter(id=str(request.auth_user.id))
            .values("refresh_token")
            .first()
        )
        connected = bool(user and user.get("refresh_token"))
    except Exception:
        connected = False
    return XeroStatusOut(connected=connected)


@session_router.post(
    "/logout",
    response={200: LogoutOut},
    summary="Invalidate the current billing session",
)
def logout(request):
    """Acknowledge a logout request and drop the user's sign-in presence.

    Billing auth cookies are set client-side (not HttpOnly) and are cleared
    by the frontend via clearAuth() in lib/auth.ts. This endpoint exists so
    the frontend has a uniform logout call and can confirm the server received
    the intent. Calling it twice is safe.

    Clearing ``signed_in_at`` is what takes the user off Minty's Settings > Users
    list — that list shows who is signed in, and the billing profile's Log out is
    one of the ways to stop being.

    ``last_seen_at`` is stamped rather than left alone, and that is load-bearing
    rather than cosmetic. Minty's Flask session survives this call on purpose (Log
    out returns the browser to the entity list, not the login page), so the user
    keeps making authenticated requests afterwards. Minty reads a cleared
    ``signed_in_at`` beside a SET ``last_seen_at`` as "signed out, leave them off",
    but beside a BLANK one as "never stamped, adopt them" — so writing both columns
    is what stops the very next Minty page from undoing this logout. See
    ``refresh_presence`` in Minty's services/user_presence.py.
    """
    user_id = getattr(request.auth_user, "id", None)
    logger.info("Logout requested user_id=%s", user_id)
    if user_id:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from shared_models.models import User

            # Naive Hong Kong local, NOT django.utils.timezone.now(). These columns
            # are TIMESTAMP WITHOUT TIME ZONE and Minty writes HK local into them
            # (models/db.py `tz`), while this project runs USE_TZ=True on UTC —
            # so timezone.now() would land eight hours behind everything Minty
            # wrote, in the same column. Mirrors user_presence.now() exactly.
            stamp = datetime.now(ZoneInfo("Asia/Hong_Kong")).replace(tzinfo=None)
            User.objects.filter(id=str(user_id)).update(
                signed_in_at=None, last_seen_at=stamp
            )
        except Exception:
            # Presence is decoration on a user list. A logout that reached us is
            # a logout, whether or not we managed to record it.
            logger.exception("Failed to clear sign-in presence user_id=%s", user_id)
    return LogoutOut(detail="logged out")
