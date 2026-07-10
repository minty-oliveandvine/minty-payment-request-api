"""Xero access-token resolution for billing.

Billing is a token *reader*. The Flask app (Module 1) is the only service that calls
Xero's /connect/token, because Xero rotates refresh tokens on every use and
immediately invalidates the one it was sent — two refreshers racing on a single-use
token leave one side holding a dead credential, and the Xero connection stays broken
until a user manually reconnects.

So: read the token the Flask app persisted; if it is missing or expired, ask the Flask
app for a fresh one over `XERO_TOKEN_SERVICE_URL`, where the refresh is serialized
behind a Postgres advisory lock.

`refresh_access_token_for_user`, `_persist_tokens_from_refresh` and
`ensure_valid_token_persist` implement the refresh path and are intentionally NOT
called from the request path. They are retained only for their unit tests and should
be deleted once nothing references them. Populating XERO_CLIENT_ID/XERO_CLIENT_SECRET
would let them run and break the Xero connection; see `.env.example`.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt
import requests
from django.conf import settings
from django.db.models import F
from django.utils import timezone as django_tz

from core.exceptions import BillValidationError
from shared_models.models import Entity, User

logger = logging.getLogger("minty-api")

HK = ZoneInfo("Asia/Hong_Kong")


def _token_expired(user: User) -> bool:
    """Return True if past expiry window or expiry metadata absent, False if still valid."""
    if user.expires_in is None or user.token_created_at is None:
        return True
    try:
        expires_in_seconds = int(user.expires_in) - 300
        tc = user.token_created_at
        if django_tz.is_aware(tc):
            created_at = tc.astimezone(HK).timestamp()
        else:
            created_at = tc.replace(tzinfo=HK).timestamp()
        timenow = datetime.now(HK).timestamp()
        elapsed_seconds = timenow - created_at
        return elapsed_seconds > expires_in_seconds
    except Exception as exc:
        logger.warning("Xero token expiry check failed for user %s: %s", user.id, exc)
        return True


def refresh_access_token_for_user(user: User) -> dict | None:
    """POST to Xero identity `/connect/token` (same as Flask `refresh_access_token_for_user`)."""
    if not user or not user.refresh_token:
        return None
    cid = getattr(settings, "XERO_CLIENT_ID", "") or ""
    secret = getattr(settings, "XERO_CLIENT_SECRET", "") or ""
    if not cid or not secret:
        logger.warning("XERO_CLIENT_ID/XERO_CLIENT_SECRET not set; cannot refresh token")
        return None
    try:
        client_id_secret = f"{cid}:{secret}"
        b64 = base64.b64encode(client_id_secret.encode("utf-8")).decode("utf-8")
        url = "https://identity.xero.com/connect/token"
        payload = {"grant_type": "refresh_token", "refresh_token": user.refresh_token}
        headers = {
            "Authorization": f"Basic {b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = requests.post(url, data=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        logger.error(
            "Xero token refresh failed: %s %s",
            resp.status_code,
            (resp.text or "")[:500],
        )
        return None
    except requests.RequestException as exc:
        logger.error("Xero token refresh request error: %s", exc)
        return None


def _persist_tokens_from_refresh(user: User, token_data: dict) -> None:
    """Write refreshed tokens to the shared user row (Flask `auto_refresh_token` fields)."""
    new_at = token_data.get("access_token") or user.access_token
    new_rt = token_data.get("refresh_token", user.refresh_token) or user.refresh_token
    new_exp = token_data.get("expires_in") or user.expires_in
    new_id = token_data.get("id_token", user.id_token) or user.id_token
    User.objects.filter(pk=user.pk).update(
        access_token=new_at,
        refresh_token=new_rt,
        expires_in=new_exp,
        id_token=new_id,
        token_created_at=django_tz.now(),
    )


def ensure_valid_token_persist(user: User) -> bool:
    """Match Flask `ensure_valid_token`: refresh if expired and persist to DB."""
    if not user.refresh_token:
        return bool(user.access_token)
    if not user.access_token:
        return False

    expired = _token_expired(user)
    if not expired:
        return True

    logger.info("Xero access token expired for user %s, refreshing", user.id)
    token_data = refresh_access_token_for_user(user)
    if not token_data:
        logger.warning("Xero token refresh returned no data for user %s", user.id)
        return False
    _persist_tokens_from_refresh(user, token_data)
    logger.info("Xero access token refreshed successfully for user %s", user.id)
    return True


def _resolve_token_user(entity_id: str, jwt_user_id: str) -> tuple[User | None, str | None]:
    """Prefer org-linked user (Flask `get_xero_token_user_for_entity`), else JWT user.

    Returns (user, failure_reason). failure_reason is None when a user is found.
    """
    entity = Entity.objects.filter(id=entity_id).first()
    jwt_user = User.objects.filter(id=jwt_user_id).first()

    if not entity or not entity.xero_org_id:
        logger.warning("Xero: entity %s has no Xero org linked", entity_id)
        return None, "no_org"

    # Several users may share an org's xero_entity_id. Without an explicit order,
    # .first() picks arbitrarily and can select a different user per request. Prefer
    # the most recently refreshed token. The Flask app resolves this deterministically
    # via entities.connected_by_user_id; billing cannot until that column is backfilled.
    owner = (
        User.objects.filter(xero_entity_id=str(entity.xero_org_id))
        .exclude(access_token__isnull=True)
        .exclude(access_token="")
        .order_by(F("token_created_at").desc(nulls_last=True), "id")
        .first()
    )
    if owner and owner.access_token:
        logger.info(
            "Xero: org-linked token user %s for entity %s",
            owner.id,
            entity_id,
        )
        return owner, None

    if jwt_user and jwt_user.access_token:
        logger.info(
            "Xero: JWT user %s token for entity %s",
            jwt_user_id,
            entity_id,
        )
        return jwt_user, None

    logger.warning(
        "Xero: no access token user for entity %s (JWT %s)",
        entity_id,
        jwt_user_id,
    )
    return None, "no_token_user"


_TOKEN_SERVICE_SCOPE = "xero-access-token"


def _request_token_from_flask(entity_id: str) -> str | None:
    """Ask the Flask app for a currently-valid access token for `entity_id`.

    The Flask app is the only service permitted to call Xero's /connect/token, and it
    serializes refreshes behind an advisory lock. `entity_id` travels inside the signed
    claims, not the body, so a leaked assertion cannot be replayed for another entity.

    Returns None on any failure; the caller then surfaces a reconnect prompt. Never
    raises — a token service outage must not become a 500 on the publish path.
    """
    url = getattr(settings, "XERO_TOKEN_SERVICE_URL", "") or ""
    secret = getattr(settings, "SECRET_KEY", "") or ""
    if not url or not secret:
        logger.error("XERO_TOKEN_SERVICE_URL/SECRET_KEY unset; cannot obtain Xero token")
        return None

    now = datetime.now(tz=timezone.utc)
    assertion = jwt.encode(
        {
            "scope": _TOKEN_SERVICE_SCOPE,
            "entity_id": str(entity_id),
            "iat": now,
            "exp": now + timedelta(seconds=60),
        },
        secret,
        algorithm="HS256",
    )

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {assertion}"},
            timeout=getattr(settings, "XERO_TOKEN_SERVICE_TIMEOUT", 15),
        )
    except requests.RequestException as exc:
        logger.error("Xero token service unreachable for entity %s: %s", entity_id, exc)
        return None

    if resp.status_code == 200:
        try:
            access_token = (resp.json() or {}).get("access_token")
        except ValueError:
            logger.error("Xero token service returned malformed JSON for entity %s", entity_id)
            return None
        if access_token:
            logger.info("Xero token service issued a token for entity %s", entity_id)
            return access_token
        logger.error("Xero token service returned no access_token for entity %s", entity_id)
        return None

    if resp.status_code == 409:
        logger.info("Xero token service: reconnect required for entity %s", entity_id)
        return None

    logger.error(
        "Xero token service error for entity %s: %s %s",
        entity_id,
        resp.status_code,
        (resp.text or "")[:200],
    )
    return None


def resolve_xero_access_token_for_entity(entity_id: str, jwt_user_id: str) -> str | None:
    """Return a currently-valid access token for the entity, or raise.

    Billing never refreshes. Xero rotates refresh tokens on use and invalidates the
    previous one, so a second refresher racing the Flask app would leave one side
    holding a dead token and break the connection until a user manually reconnects.
    When the locally-stored token is missing or expired, billing asks the Flask app,
    which refreshes behind a lock. `ensure_valid_token_persist` is deliberately not
    called here — see the module docstring.

    A token that is merely *present* is not usable. Xero access tokens live ~30 minutes,
    so an unexpired check is required before returning one; otherwise Xero rejects the
    subsequent API call with 403 AuthenticationUnsuccessful.
    """
    user, failure_reason = _resolve_token_user(entity_id, jwt_user_id)
    if failure_reason == "no_org":
        raise BillValidationError(
            "Entity is not linked to a Xero organization. Contact your administrator."
        )

    # Fast path: the Flask app refreshes on its own traffic and mirrors the result
    # to the user row, so most publishes find a live token without a round trip.
    if user is not None:
        user.refresh_from_db()
        if user.access_token and not _token_expired(user):
            return user.access_token

    access_token = _request_token_from_flask(entity_id)
    if access_token:
        return access_token

    logger.warning(
        "Xero: no valid access token for entity %s (token user %s); reconnect required",
        entity_id,
        getattr(user, "id", None),
    )
    raise BillValidationError(
        "Your Xero connection has expired. Please reconnect to Xero."
    )
