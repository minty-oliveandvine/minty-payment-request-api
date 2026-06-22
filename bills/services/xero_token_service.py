"""Xero OAuth token refresh — mirrors Module 1 Flask `ensure_valid_token` / `auto_refresh_token`.

Resolves which user's tokens to use (org-linked owner vs JWT user), refreshes when
expired, and persists new tokens to the shared `user` table so Module 1 and Module 2
stay aligned.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
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

    owner = (
        User.objects.filter(xero_entity_id=str(entity.xero_org_id))
        .exclude(access_token__isnull=True)
        .exclude(access_token="")
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


def resolve_xero_access_token_for_entity(entity_id: str, jwt_user_id: str) -> str | None:
    """Return a usable access token after refresh-if-needed; persist like Module 1.

    Concurrent refresh race: Xero refresh tokens are single-use. If two requests both
    find the token expired, both attempt a refresh. The second caller's POST returns 400
    and `ensure_valid_token_persist` returns False. The subsequent `refresh_from_db()`
    in the failure branch re-reads the row written by the first (winning) caller before
    checking `user.access_token`. If the winner persisted a valid token the check passes
    and the loser proceeds with that token — no error is surfaced unnecessarily.
    """
    user, failure_reason = _resolve_token_user(entity_id, jwt_user_id)
    if not user:
        if failure_reason == "no_org":
            raise BillValidationError(
                "Entity is not linked to a Xero organization. Contact your administrator."
            )
        raise BillValidationError(
            "No Xero account is connected for this entity. Please reconnect to Xero."
        )
    if not ensure_valid_token_persist(user):
        # Refresh failed — re-read DB in case a concurrent request already refreshed.
        user.refresh_from_db()
        if not user.access_token:
            raise BillValidationError(
                "Your Xero connection has expired. Please reconnect to Xero."
            )
    else:
        user.refresh_from_db()
    return user.access_token or None
