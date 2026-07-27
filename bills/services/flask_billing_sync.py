"""
Ask Flask (Module 1) to refresh entity_bill_account_xero from Xero.

Uses FLASK_APP_URL (base URL only, e.g. http://localhost:5001). Debounced
per entity via Django cache to avoid hammering Xero when the UI polls lists.
"""

import json
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("minty-api")

# Debounce window: skip duplicate triggers for the same entity (seconds).
_BILL_CHART_SYNC_TTL = 75
_CHART_CHANGE_SYNC_TTL = 120
_CONTACT_SYNC_TTL = 120


def trigger_flask_bill_chart_sync(
    request, entity_id: str, *, force: bool = False
) -> bool:
    """POST to Flask JWT sync endpoint; failures are logged only.

    Returns True if the sync request was sent (regardless of Flask response
    status), False if the sync was skipped or could not be attempted.

    When force is False, skips duplicate triggers for the same entity within
    _BILL_CHART_SYNC_TTL (e.g. list polling). Use force=True on Module 2
    settings so Xero reconciliation always runs when the user opens settings.
    """
    base = (getattr(settings, "FLASK_APP_URL", "") or "").rstrip("/")
    if not base:
        logger.error(
            "FLASK_APP_URL not set; bill chart sync skipped entity=%s — "
            "accounts list may be empty or stale",
            entity_id,
        )
        return False

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False

    cache_key = f"bill_chart_sync:{entity_id}"
    if not force:
        if not cache.add(cache_key, 1, timeout=_BILL_CHART_SYNC_TTL):
            return False

    url = f"{base}/api/entities/{entity_id}/billing/sync-chart-accounts"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": auth,
                "X-Entity-Id": entity_id,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error(
                "Flask bill chart sync HTTP %s entity=%s body=%s — "
                "accounts list may be empty or stale",
                resp.status_code,
                entity_id,
                (resp.text or "")[:500],
            )
        else:
            try:
                body = resp.json()
                if not isinstance(body, dict):
                    logger.warning(
                        "Flask bill chart sync unexpected JSON type entity=%s "
                        "status=%s body_preview=%s",
                        entity_id,
                        resp.status_code,
                        str(body)[:400],
                    )
                elif body.get("skipped"):
                    logger.warning(
                        "Flask bill chart sync skipped entity=%s reason=%s detail=%s",
                        entity_id,
                        body.get("reason"),
                        body.get("error") or body.get("detail") or "",
                    )
                else:
                    logger.info(
                        "Flask bill chart sync ok entity=%s new_rows=%s",
                        entity_id,
                        body.get("new_rows"),
                    )
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning(
                    "Flask bill chart sync non-JSON response entity=%s status=%s "
                    "content_type=%s body_preview=%s",
                    entity_id,
                    resp.status_code,
                    resp.headers.get("Content-Type", ""),
                    (resp.text or "")[:400],
                )
        return True
    except requests.RequestException as exc:
        logger.error(
            "Flask bill chart sync request failed entity=%s: %s — "
            "accounts list may be empty or stale",
            entity_id,
            exc,
        )
        return False


def trigger_chart_sync_if_changed(
    request, entity_id: str, *, force: bool = False
) -> bool:
    """Compare Xero live vs DB and sync both modules if changes detected.

    Returns True if the sync request was sent, False if skipped or failed.
    Debounced per entity with _CHART_CHANGE_SYNC_TTL. Use force=True to bypass.
    """
    base = (getattr(settings, "FLASK_APP_URL", "") or "").rstrip("/")
    if not base:
        logger.error(
            "FLASK_APP_URL not set; chart change sync skipped entity=%s — "
            "accounts list may be empty or stale",
            entity_id,
        )
        return False

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False

    cache_key = f"chart_change_sync:{entity_id}"
    if not force:
        if not cache.add(cache_key, 1, timeout=_CHART_CHANGE_SYNC_TTL):
            return False

    url = f"{base}/api/entities/{entity_id}/billing/sync-chart-if-changed"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": auth, "X-Entity-Id": entity_id},
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error(
                "Chart change sync HTTP %s entity=%s body=%s — "
                "accounts list may be empty or stale",
                resp.status_code,
                entity_id,
                (resp.text or "")[:500],
            )
        else:
            try:
                body = resp.json()
                if body.get("changed"):
                    logger.info(
                        "Chart change sync: changes detected and synced entity=%s m1=%s m2=%s",
                        entity_id,
                        body.get("module1_synced"),
                        body.get("module2_synced"),
                    )
                elif body.get("skipped"):
                    logger.info(
                        "Chart change sync skipped entity=%s reason=%s",
                        entity_id,
                        body.get("reason"),
                    )
                else:
                    logger.info("Chart change sync: no changes entity=%s", entity_id)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return True
    except requests.RequestException as exc:
        logger.error(
            "Chart change sync request failed entity=%s: %s — "
            "accounts list may be empty or stale",
            entity_id,
            exc,
        )
        return False


def trigger_flask_contact_sync(request, entity_id: str, *, force: bool = False) -> None:
    """POST to Flask contact sync endpoint; failures are logged only.

    Debounced per entity via Django cache (_CONTACT_SYNC_TTL). Use force=True
    to bypass debounce (e.g. after a Xero reconnect).
    """
    base = (getattr(settings, "FLASK_APP_URL", "") or "").rstrip("/")
    if not base:
        logger.warning("FLASK_APP_URL not set; contact sync skipped")
        return

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return

    cache_key = f"contact_sync:{entity_id}"
    if not force:
        if not cache.add(cache_key, 1, timeout=_CONTACT_SYNC_TTL):
            return

    url = f"{base}/api/entities/{entity_id}/billing/sync-contacts-if-changed"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": auth,
                "X-Entity-Id": entity_id,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Flask contact sync HTTP %s entity=%s body=%s",
                resp.status_code,
                entity_id,
                (resp.text or "")[:500],
            )
        else:
            try:
                body = resp.json()
                if not isinstance(body, dict):
                    logger.warning(
                        "Flask contact sync unexpected JSON type entity=%s "
                        "status=%s body_preview=%s",
                        entity_id,
                        resp.status_code,
                        str(body)[:400],
                    )
                elif body.get("skipped"):
                    logger.info(
                        "Flask contact sync skipped entity=%s reason=%s",
                        entity_id,
                        body.get("reason"),
                    )
                else:
                    logger.info(
                        "Flask contact sync ok entity=%s changed=%s "
                        "inserted=%s updated=%s deleted=%s",
                        entity_id,
                        body.get("changed"),
                        body.get("inserted"),
                        body.get("updated"),
                        body.get("deleted"),
                    )
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning(
                    "Flask contact sync non-JSON response entity=%s status=%s "
                    "content_type=%s body_preview=%s",
                    entity_id,
                    resp.status_code,
                    resp.headers.get("Content-Type", ""),
                    (resp.text or "")[:400],
                )
    except requests.RequestException as exc:
        logger.warning(
            "Flask contact sync request failed entity=%s: %s",
            entity_id,
            exc,
        )
