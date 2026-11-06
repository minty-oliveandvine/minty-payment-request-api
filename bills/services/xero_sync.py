"""Refresh the company's Xero accounts and contacts - accounts-api's one sync
(``POST /api/internal/xero/sync``; Phase 4 of the Flask retirement, 2026-11-06).

It replaces ``flask_billing_sync.py``. That called Flask's ``/api/entities/<id>/billing/sync-*``
routes, which only checked that the header began with ``Bearer `` and so were open to anyone.
Now one call refreshes both halves: ``account_info``, which ``entity_bill_account_xero`` (the Bill
CoA list) is mirrored from, and ``xero_contact_sync``. The call is a 60-second assertion scoped
``xero-sync``, and the company is in its claims.

Fire and forget, as before: the list and settings screens render from the tables at once, and the
sync runs on a background thread. It is debounced per company (``SYNC_TTL``), so a polling list
doesn't hammer Xero; ``force`` skips the debounce. A failed sync is logged, never raised: the
screens show what is stored.
"""

from __future__ import annotations

import logging
import threading

import requests
from django.conf import settings
from django.core.cache import cache
from minty_shared import logctx, tokens

logger = logging.getLogger("minty-api")

SCOPE = "xero-sync"
#: Seconds a company's sync isn't asked for again (Flask's routes were debounced 75-120 s).
SYNC_TTL = 120
#: Two Xero fetches (the accounts, then every page of contacts) behind accounts-api.
TIMEOUT = (5, 60)


def run_sync(entity_id: str) -> dict | None:
    """Ask accounts-api to sync the company now. Its counts, or None when it could not (logged)."""
    url = getattr(settings, "XERO_SYNC_URL", "") or ""
    secret = getattr(settings, "SECRET_KEY", "") or ""
    if not url or not secret:
        logger.error("XERO_SYNC_URL/SECRET_KEY unset; Xero sync skipped entity=%s", entity_id)
        return None
    assertion = tokens.service_assertion(secret, SCOPE, entity_id)
    try:
        resp = requests.post(url, headers=tokens.assertion_headers(assertion), timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Xero sync unreachable entity=%s: %s - accounts and contacts may be stale", entity_id, exc)
        return None
    if resp.status_code == 409:
        logger.info("Xero sync: reconnect required entity=%s", entity_id)
        return None
    if resp.status_code != 200:
        logger.error("Xero sync HTTP %s entity=%s body=%s", resp.status_code, entity_id, (resp.text or "")[:300])
        return None
    try:
        body = resp.json()
    except ValueError:
        logger.error("Xero sync answered non-JSON entity=%s", entity_id)
        return None
    logger.info("Xero sync entity=%s accounts=%s contacts=%s", entity_id, body.get("accounts"),
                body.get("contacts"))
    return body


def request_sync(entity_id: str, *, force: bool = False) -> bool:
    """Start the company's sync in the background unless one was asked for within ``SYNC_TTL``.
    True when started."""
    if not force and not cache.add(f"xero_sync:{entity_id}", 1, timeout=SYNC_TTL):
        return False
    threading.Thread(target=logctx.carry_context(run_sync), args=(str(entity_id),), daemon=True).start()
    return True
