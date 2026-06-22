"""Fetch contacts for the bill contact dropdown.

Mirrors Module 1 (Petty Cash) behaviour:
  1. If entity is connected → call Xero Contacts API (live, full list), then merge in
     any ``xero_contact_sync`` rows for the same entity whose ``xero_contact_id`` is
     not in the live response (Xero list can lag right after POST create). Dedupe by
     ``xero_contact_id``; category filter matches the DB path when applied.
  2. If Xero fails or entity is disconnected → fall back to xero_contact_sync DB only.
  3. DB fallback returns ALL contacts for the entity (no xero_org_id filter).
  4. Xero token: `xero_token_service.resolve_xero_access_token_for_entity` (owner/JWT +
     refresh-if-expired, same as Module 1).
  5. Before returning, contacts are deduped by Xero ContactID, then by normalized
     display name (Xero often returns multiple contact records with the same name).
"""

import logging
import os
import uuid

import requests

from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.exceptions import BillValidationError
from shared_models.models import Entity, XeroContactSync

logger = logging.getLogger("minty-api")

XERO_API_BASE_URL = os.environ.get(
    "XERO_API_BASE_URL", "https://api.xero.com/api.xro/2.0"
)


def _fetch_contacts_from_xero(access_token: str, xero_org_id: str) -> list[dict]:
    """Call Xero GET /Contacts with pagination. Returns raw Xero contact dicts."""
    all_contacts: list[dict] = []
    page = 1

    while True:
        url = (
            f"{XERO_API_BASE_URL}/Contacts"
            f"?page={page}&pageSize=1000&order=Name%20ASC"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-Tenant-Id": str(xero_org_id),
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            logger.warning("Xero contacts request failed: %s", exc)
            break

        if resp.status_code != 200:
            logger.warning(
                "Xero contacts API returned %s: %s",
                resp.status_code,
                (resp.text or "")[:500],
            )
            break

        contacts = resp.json().get("Contacts", [])
        if not contacts:
            break

        all_contacts.extend(contacts)
        if len(contacts) < 1000:
            break
        page += 1

    return all_contacts


def _xero_to_bill_contact(contact: dict, entity_id: str, xero_org_id: str) -> dict:
    """Normalise a Xero API contact dict to EntityBillContactOut shape."""
    return {
        "id": contact.get("ContactID", ""),
        "entity_id": entity_id,
        "xero_contact_id": contact.get("ContactID", ""),
        "xero_org_id": xero_org_id,
        "name": contact.get("Name", ""),
        "category": None,
    }


def _db_to_bill_contact(row: XeroContactSync) -> dict:
    """Normalise a DB row to EntityBillContactOut shape."""
    return {
        "id": row.xero_contact_id,
        "entity_id": row.entity_id,
        "xero_contact_id": row.xero_contact_id,
        "xero_org_id": row.xero_org_id or None,
        "name": row.name,
        "category": row.category,
    }


def _dedupe_bill_contacts_by_xero_contact_id(contacts: list[dict]) -> list[dict]:
    """Keep the first row per non-empty xero_contact_id (strip, case-insensitive)."""
    seen: set[str] = set()
    out: list[dict] = []
    for c in contacts:
        raw = (c.get("xero_contact_id") or "").strip()
        if raw:
            key = raw.upper()
            if key in seen:
                continue
            seen.add(key)
        out.append(c)
    return out


def _normalize_contact_name_for_dedupe(name: str | None) -> str:
    """Collapse whitespace; casefold for stable comparison."""
    return " ".join((name or "").split()).casefold()


def _dedupe_bill_contacts_by_normalized_name(contacts: list[dict]) -> list[dict]:
    """Keep the first row per normalized display name (Xero often has multiple cards per name).

    Runs after ID dedupe. Rows with an empty normalized name are kept (unchanged order).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for c in contacts:
        norm = _normalize_contact_name_for_dedupe(c.get("name"))
        if not norm:
            out.append(c)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(c)
    return out


def _merge_db_contacts_missing_from_live(
    live_contacts: list[dict],
    entity_id: str,
    category: str | None,
) -> list[dict]:
    """Append ``xero_contact_sync`` rows not returned by live Xero GET.

    Newly created contacts are written to the DB immediately; Xero's list
    endpoint can lag behind, so a live-only list would hide them until refresh.
    """
    seen = {
        (c.get("xero_contact_id") or "").strip().upper()
        for c in live_contacts
        if (c.get("xero_contact_id") or "").strip()
    }
    qs = XeroContactSync.objects.filter(entity_id=entity_id)
    if category:
        categories = [c.strip() for c in category.split(",") if c.strip()]
        qs = qs.filter(category__in=categories)
    extras: list[dict] = []
    for row in qs.order_by("name"):
        cid = (row.xero_contact_id or "").strip().upper()
        if cid and cid not in seen:
            seen.add(cid)
            extras.append(_db_to_bill_contact(row))
    if not extras:
        return live_contacts
    return live_contacts + extras


def get_entity_bill_contacts(
    entity_id: str,
    *,
    jwt_user_id: str,
    category: str | None = None,
) -> list[dict]:
    """Return contacts for the add-bill dropdown, matching Module 1 logic.

    When a category filter is requested we always use the DB path because Xero
    contacts carry no category field — the live fetch would silently return an
    empty list after the post-filter.
    """
    entity = Entity.objects.filter(id=entity_id).first()
    is_connected = entity and entity.status == "connected" and entity.xero_org_id
    xero_org_id = entity.xero_org_id if entity else ""

    contacts: list[dict] | None = None

    try:
        access_token = resolve_xero_access_token_for_entity(entity_id, jwt_user_id)
    except BillValidationError as exc:
        logger.warning("Xero token unavailable for contact fetch, falling back to DB: %s", exc)
        access_token = None

    # Skip live Xero fetch when a category filter is requested: Xero contacts
    # have no category field, so fetching from Xero and then filtering by
    # category would always produce an empty result.
    use_live_xero = is_connected and access_token and not category

    if use_live_xero:
        logger.info(
            "Entity %s is connected, fetching contacts from Xero API", entity_id
        )
        xero_contacts = _fetch_contacts_from_xero(access_token, xero_org_id)
        contacts = [
            _xero_to_bill_contact(c, entity_id, xero_org_id) for c in xero_contacts
        ]
        contacts = _merge_db_contacts_missing_from_live(
            contacts, entity_id, category
        )
        logger.info(
            "Entity %s: %d from Xero API, %d total after DB merge",
            entity_id,
            len(xero_contacts),
            len(contacts),
        )

    if contacts is None:
        logger.info(
            "Falling back to DB contacts for entity %s", entity_id
        )
        qs = XeroContactSync.objects.filter(entity_id=entity_id)
        if category:
            categories = [c.strip() for c in category.split(",") if c.strip()]
            qs = qs.filter(category__in=categories)
        contacts = [_db_to_bill_contact(row) for row in qs.order_by("name")]
        logger.info(
            "Loaded %d contacts from DB for entity %s", len(contacts), entity_id
        )

    contacts.sort(
        key=lambda c: (
            (c.get("name") or "").lower(),
            c.get("xero_contact_id") or "",
        )
    )
    contacts = _dedupe_bill_contacts_by_xero_contact_id(contacts)
    contacts = _dedupe_bill_contacts_by_normalized_name(contacts)
    return contacts


def create_entity_bill_contact_in_xero(
    entity_id: str,
    *,
    jwt_user_id: str,
    name: str,
) -> dict:
    """Create a supplier/customer contact in Xero and upsert ``xero_contact_sync``.

    Requires the entity to be Xero-connected (same precondition as live GET list).
    Returns the same dict shape as list items (``EntityBillContactOut``).
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise BillValidationError("Contact name is required")
    if len(cleaned) > 150:
        raise BillValidationError("Contact name must be at most 150 characters")

    entity = Entity.objects.filter(id=entity_id).first()
    if not entity:
        raise BillValidationError("Entity not found")
    if entity.status != "connected" or not entity.xero_org_id:
        raise BillValidationError(
            "Entity must be connected to Xero to create contacts"
        )

    access_token = resolve_xero_access_token_for_entity(entity_id, jwt_user_id)
    if not access_token:
        raise BillValidationError(
            "Could not resolve Xero access token for this entity"
        )

    xero_org_id = str(entity.xero_org_id)
    url = f"{XERO_API_BASE_URL}/Contacts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {"Contacts": [{"Name": cleaned}]}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
    except requests.RequestException as exc:
        logger.warning("Xero create contact request failed: %s", exc)
        raise BillValidationError(
            "Could not reach Xero to create the contact. Try again."
        ) from exc

    if resp.status_code != 200:
        detail = (resp.text or "")[:500]
        logger.warning(
            "Xero create contact returned %s: %s",
            resp.status_code,
            detail,
        )
        try:
            err_json = resp.json()
            msg = err_json.get("Detail") or err_json.get("Message")
            if isinstance(msg, str) and msg.strip():
                raise BillValidationError(msg.strip())
            elements = err_json.get("Elements")
            if isinstance(elements, list) and elements:
                first = elements[0]
                if isinstance(first, dict):
                    ve = first.get("ValidationErrors")
                    if isinstance(ve, list) and ve:
                        m = ve[0].get("Message") if isinstance(ve[0], dict) else None
                        if isinstance(m, str) and m.strip():
                            raise BillValidationError(m.strip())
        except BillValidationError:
            raise
        except (ValueError, TypeError, KeyError):
            pass
        raise BillValidationError(
            "Xero rejected the new contact. Check the name and try again."
        )

    try:
        payload = resp.json()
    except ValueError:
        raise BillValidationError("Invalid response from Xero after creating contact")

    xero_contacts = payload.get("Contacts") or []
    if not xero_contacts:
        raise BillValidationError("Xero did not return the new contact")
    xc = xero_contacts[0]
    contact_id = (xc.get("ContactID") or "").strip()
    contact_name = (xc.get("Name") or cleaned).strip()[:150]
    if not contact_id:
        raise BillValidationError("Xero did not return a contact id")

    row = XeroContactSync.objects.filter(
        entity_id=entity_id,
        xero_contact_id=contact_id,
    ).first()
    if row:
        row.name = contact_name
        row.xero_org_id = xero_org_id
        row.save(update_fields=["name", "xero_org_id"])
    else:
        row = XeroContactSync.objects.create(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            xero_contact_id=contact_id,
            xero_org_id=xero_org_id,
            name=contact_name,
            category=None,
        )

    return _db_to_bill_contact(row)
