"""Xero access-token resolution for billing.

Billing is a token *reader*. The Flask app (Module 1) is the only service that calls
Xero's /connect/token, because Xero rotates refresh tokens on every use and
immediately invalidates the one it was sent — two refreshers racing on a single-use
token leave one side holding a dead credential, and the Xero connection stays broken
until a user manually reconnects.

So: read the token Minty persisted in ``user_token`` (the row of the member who connected
the company, ``entities.connected_by_user_id``); if it is missing or expired, ask the
Flask app for a fresh one over `XERO_TOKEN_SERVICE_URL`, where the refresh is serialized
behind a Postgres advisory lock.

The refresh path this module once carried for its unit tests
(`refresh_access_token_for_user`, `_persist_tokens_from_refresh`,
`ensure_valid_token_persist`) is gone with C1 of docs/modernisation/modernisation_plan.md: the six
token columns left ``user``, and billing has no business writing ``user_token``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
import requests
from django.conf import settings
from django.utils import timezone as django_tz

from core.exceptions import BillValidationError
from shared_models.models import Entity, UserToken

logger = logging.getLogger("minty-api")

# Treat a token as spent this many seconds before Xero does, so a publish that starts
# right at the edge does not carry a dead credential into a multi-call sequence.
EXPIRY_MARGIN_SECONDS = 300


def _token_expired(token: UserToken) -> bool:
    """True past the expiry window or when the expiry pair is absent, else False.

    ``access_token_obtained_at`` is ``timestamptz``; Postgres hands it back aware, SQLite
    (tests) naive-as-UTC.
    """
    if token.access_token_expires_in is None or token.access_token_obtained_at is None:
        return True
    try:
        obtained = token.access_token_obtained_at
        if not django_tz.is_aware(obtained):
            obtained = obtained.replace(tzinfo=timezone.utc)
        elapsed = (django_tz.now() - obtained).total_seconds()
        return elapsed > int(token.access_token_expires_in) - EXPIRY_MARGIN_SECONDS
    except Exception as exc:
        logger.warning("Xero token expiry check failed for token %s: %s", token.pk, exc)
        return True


def _resolve_token(
    entity_id: str, jwt_user_id: str
) -> tuple[UserToken | None, str | None]:
    """The connector's token row (``entities.connected_by_user_id``), else the JWT user's.

    Returns (token, failure_reason). failure_reason is None when a row with an access
    token was found.
    """
    entity = Entity.objects.filter(id=entity_id).first()

    if not entity or not entity.xero_org_id:
        logger.warning("Xero: entity %s has no Xero org linked", entity_id)
        return None, "no_org"

    candidates: list[str] = []
    if entity.connected_by_user_id:
        candidates.append(str(entity.connected_by_user_id))
    if jwt_user_id and str(jwt_user_id) not in candidates:
        candidates.append(str(jwt_user_id))

    for user_id in candidates:
        token = (
            UserToken.objects.filter(user_id=user_id)
            .exclude(access_token__isnull=True)
            .exclude(access_token="")
            .first()
        )
        if token is not None:
            logger.info("Xero: token user %s for entity %s", user_id, entity_id)
            return token, None

    logger.warning(
        "Xero: no access token user for entity %s (JWT %s)", entity_id, jwt_user_id
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
        logger.error(
            "XERO_TOKEN_SERVICE_URL/SECRET_KEY unset; cannot obtain Xero token"
        )
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
            logger.error(
                "Xero token service returned malformed JSON for entity %s", entity_id
            )
            return None
        if access_token:
            logger.info("Xero token service issued a token for entity %s", entity_id)
            return access_token
        logger.error(
            "Xero token service returned no access_token for entity %s", entity_id
        )
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


def resolve_xero_access_token_for_entity(
    entity_id: str, jwt_user_id: str
) -> str | None:
    """Return a currently-valid access token for the entity, or raise.

    Billing never refreshes. Xero rotates refresh tokens on use and invalidates the
    previous one, so a second refresher racing the Flask app would leave one side
    holding a dead token and break the connection until a user manually reconnects.
    When the stored token is missing or expired, billing asks the Flask app, which
    refreshes behind a lock.

    A token that is merely *present* is not usable. Xero access tokens live ~30 minutes,
    so an unexpired check is required before returning one; otherwise Xero rejects the
    subsequent API call with 403 AuthenticationUnsuccessful.
    """
    token, failure_reason = _resolve_token(entity_id, jwt_user_id)
    if failure_reason == "no_org":
        raise BillValidationError(
            "Entity is not linked to a Xero organization. Contact your administrator."
        )

    # Fast path: the Flask app refreshes on its own traffic and the result is in
    # user_token, so most publishes find a live token without a round trip.
    if token is not None and token.access_token and not _token_expired(token):
        return token.access_token

    access_token = _request_token_from_flask(entity_id)
    if access_token:
        return access_token

    logger.warning(
        "Xero: no valid access token for entity %s (token user %s); reconnect required",
        entity_id,
        getattr(token, "user_id", None),
    )
    raise BillValidationError(
        "Your Xero connection has expired. Please reconnect to Xero."
    )
