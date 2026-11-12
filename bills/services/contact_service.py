"""Fetch contacts for the bill contact dropdown.

Mirrors Module 1 (Petty Cash) behaviour:
  1. If entity is connected → call Xero Contacts API (live, full list), then merge in
     any ``xero_contact_sync`` rows for the same entity whose ``xero_contact_id`` is
     not in the live response (Xero list can lag right after POST create). Dedupe by
     ``xero_contact_id``; category filter matches the DB path when applied.
  2. If Xero fails or entity is disconnected → fall back to xero_contact_sync DB only.
  3. DB reads are scoped to the entity's CURRENT Xero org, plus legacy rows
     written before xero_org_id was populated. An entity that is reconnected
     to a different Xero organisation keeps its old contact rows, and those
     contact ids do not exist in the new org.
  4. Xero token: `xero_token_service.resolve_xero_access_token_for_entity` (owner/JWT +
     refresh-if-expired, same as Module 1).
  5. Before returning, contacts are deduped by Xero ContactID, then by normalized
     display name (Xero often returns multiple contact records with the same name).
"""

import logging
import uuid

import requests
from django.db import IntegrityError, transaction
from django.db.models import Q
from minty_db.models import Entity, XeroContact, XeroContactSync

from bills.services.books import minty_on_its_own
from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.exceptions import BillValidationError

logger = logging.getLogger("minty-api")


def _xero_api_base() -> str:
    # Imported lazily: xero_publish_service imports this module at load time.
    from bills.services.xero_publish_service import XERO_API_BASE

    return XERO_API_BASE


def _fetch_contacts_from_xero(access_token: str, xero_org_id: str) -> list[dict]:
    """Call Xero GET /Contacts with pagination. Returns raw Xero contact dicts."""
    all_contacts: list[dict] = []
    page = 1

    while True:
        url = (
            f"{_xero_api_base()}/Contacts"
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
    # a supplier kept only in Minty (Xero optional, 2026-11-10) has no Xero id: its row id
    public = row.xero_contact_id or str(row.id)
    return {
        "id": public,
        "entity_id": str(row.entity_id) if row.entity_id else None,  # uuid column since C5
        "xero_contact_id": public,
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


def xero_org_scope(xero_org_id) -> Q:
    """Match xero_contact_sync rows belonging to ``xero_org_id``.

    Rows with no org recorded are included: the column was added after some
    rows were written, and both writers stamp it now, so an empty value means
    "legacy" rather than "another org". Excluding them would hide contacts
    that are almost certainly still valid.

    When the entity itself has no org (never connected, or disconnected) there
    is nothing to scope against, so everything matches. Scoping to "" there
    would empty the DB fallback, which exists precisely for when Xero cannot
    be reached.
    """
    if not xero_org_id:
        return Q()
    return (
        Q(xero_org_id=str(xero_org_id))
        | Q(xero_org_id="")
        | Q(xero_org_id__isnull=True)
    )


def _merge_db_contacts_missing_from_live(
    live_contacts: list[dict],
    entity_id: str,
    category: str | None,
    xero_org_id: str | None = None,
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
    qs = XeroContactSync.objects.filter(entity_id=entity_id, record_status="active", merged_into__isnull=True)
    if xero_org_id is not None:
        # None means "caller did not scope this" and keeps the unfiltered
        # behaviour; the production caller always passes the entity org.
        qs = qs.filter(xero_org_scope(xero_org_id))
    if category:
        categories = [c.strip() for c in category.split(",") if c.strip()]
        qs = qs.filter(category__in=categories)
    extras: list[dict] = []
    for row in qs.order_by("name"):
        cid = (row.xero_contact_id or "").strip().upper()
        if not cid:
            # the company's own supplier, made while offline (owner, 2026-11-12): listed too; the
            # next live sync, or this bill's publish, puts it in Xero
            extras.append(_db_to_bill_contact(row))
        elif cid not in seen:
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
        logger.warning(
            "Xero token unavailable for contact fetch, falling back to DB: %s", exc
        )
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
            contacts, entity_id, category, xero_org_id
        )
        logger.info(
            "Entity %s: %d from Xero API, %d total after DB merge",
            entity_id,
            len(xero_contacts),
            len(contacts),
        )

    if contacts is None:
        logger.info("Falling back to DB contacts for entity %s", entity_id)
        qs = XeroContactSync.objects.filter(
            entity_id=entity_id, record_status="active", merged_into__isnull=True
        ).filter(xero_org_scope(xero_org_id))
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


def _key(name) -> str:
    return " ".join(str(name or "").split()).lower()


def held_contact(entity_id: str, name: str) -> XeroContactSync | None:
    """The company's active supplier of that name (case and inner spaces ignored), if any."""
    return next(
        (
            row
            for row in XeroContactSync.objects.filter(
                entity_id=entity_id, record_status="active", merged_into__isnull=True
            )
            if _key(row.name) == _key(name)
        ),
        None,
    )


def _kept_in_minty(entity_id: str, name: str) -> dict:
    """No live connection (owner, 2026-11-12; was: only a company keeping its books in Minty): the
    supplier is the company's own row, with no Xero call - the next live sync puts it in Xero."""
    row = XeroContactSync.objects.create(entity_id=entity_id, name=" ".join(name.split()), source="user")
    logger.info("Supplier %s kept in Minty for company %s", row.id, entity_id)
    return _db_to_bill_contact(row)


def _xero_refusal(resp) -> str:
    """Xero's own reason for a refused contact, as a sentence for the person."""
    try:
        err_json = resp.json()
        msg = err_json.get("Detail") or err_json.get("Message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        elements = err_json.get("Elements")
        if isinstance(elements, list) and elements and isinstance(elements[0], dict):
            ve = elements[0].get("ValidationErrors")
            if isinstance(ve, list) and ve and isinstance(ve[0], dict):
                m = ve[0].get("Message")
                if isinstance(m, str) and m.strip():
                    return m.strip()
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return "Xero rejected the new contact. Check the name and try again."


def xero_contact_for(entity: Entity, access_token: str, name: str) -> tuple[str, str]:
    """``(ContactID, name)`` in the company's Xero for ``name``: the stored Xero contact of that
    name the company doesn't hold yet (no duplicate in Xero), else one made there. Raises
    ``BillValidationError`` with Xero's reason. The "+ Add" and the publish heal share it."""
    xero_org_id = str(entity.xero_org_id)
    held = set(
        XeroContactSync.objects.filter(entity_id=entity.id, xero_contact_id__isnull=False).values_list(
            "xero_contact_id", flat=True
        )
    )
    copy = next(
        (
            c
            for c in XeroContact.objects.filter(entity_id=entity.id, xero_org_id=xero_org_id, status="ACTIVE")
            if _key(c.name) == _key(name) and c.xero_guid not in held
        ),
        None,
    )
    if copy is not None:
        return copy.xero_guid, " ".join(name.split())[:150]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            f"{_xero_api_base()}/Contacts", headers=headers, json={"Contacts": [{"Name": name}]}, timeout=30
        )
    except requests.RequestException as exc:
        logger.warning("Xero create contact request failed: %s", exc)
        raise BillValidationError("Could not reach Xero to create the contact. Try again.") from exc
    if resp.status_code != 200:
        logger.warning("Xero create contact returned %s: %s", resp.status_code, (resp.text or "")[:500])
        raise BillValidationError(_xero_refusal(resp))
    try:
        payload = resp.json()
    except ValueError:
        raise BillValidationError("Xero replied in a shape I didn't expect. Mind trying again?") from None
    xero_contacts = payload.get("Contacts") or []
    xc = xero_contacts[0] if xero_contacts else {}
    contact_id = (xc.get("ContactID") or "").strip()
    if not contact_id:
        raise BillValidationError("Xero didn't confirm that contact. Mind trying again?")
    return contact_id, (xc.get("Name") or name).strip()[:150]


def _active_in_xero(entity: Entity, access_token: str, guid: str) -> None:
    """Set an archived Xero contact ACTIVE again, and Minty's copy of it with it."""
    xero_org_id = str(entity.xero_org_id)
    try:
        resp = requests.post(
            f"{_xero_api_base()}/Contacts",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Xero-Tenant-Id": xero_org_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"Contacts": [{"ContactID": guid, "ContactStatus": "ACTIVE"}]},
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.warning("Xero bring-back contact request failed: %s", exc)
        raise BillValidationError("Could not reach Xero to bring the contact back. Try again.") from exc
    if resp.status_code != 200:
        logger.warning("Xero bring-back contact returned %s: %s", resp.status_code, (resp.text or "")[:500])
        raise BillValidationError(_xero_refusal(resp))
    XeroContact.objects.filter(entity_id=entity.id, xero_org_id=xero_org_id, xero_guid=guid).update(status="ACTIVE")


def _brought_back(entity: Entity, name: str, offline: bool, token_for) -> XeroContactSync | None:
    """The supplier the company deleted with this name, brought back (onboarding-api's chart rule,
    owner 2026-11-12): its archived row active again, or Xero's archived contact of the name linked
    again - ACTIVE in Xero when it is there. Offline, one still linked to Xero waits."""
    gone = sorted(
        (
            row
            for row in XeroContactSync.objects.filter(
                entity_id=entity.id, record_status="archived", merged_into__isnull=True
            )
            if _key(row.name) == _key(name)
        ),
        key=lambda row: not row.xero_contact_id,
    )
    if gone:
        row = gone[0]
        if row.xero_contact_id:
            if offline:
                raise BillValidationError(
                    f"{row.name} was archived in Xero. Ask an admin to reconnect Xero to bring it back."
                )
            _active_in_xero(entity, token_for(), row.xero_contact_id)
        XeroContactSync.objects.filter(id=row.id).update(record_status="active")
        row.record_status = "active"
        logger.info("Supplier %s brought back for company %s", row.id, entity.id)
        return row
    if offline:
        return None
    held = set(
        XeroContactSync.objects.filter(entity_id=entity.id, xero_contact_id__isnull=False).values_list(
            "xero_contact_id", flat=True
        )
    )
    copy = next(
        (
            c
            for c in XeroContact.objects.filter(entity_id=entity.id, xero_org_id=str(entity.xero_org_id)).exclude(
                status="ACTIVE"
            )
            if _key(c.name) == _key(name) and c.xero_guid not in held
        ),
        None,
    )
    if copy is None:
        return None
    _active_in_xero(entity, token_for(), copy.xero_guid)
    row = XeroContactSync.objects.create(
        entity_id=entity.id,
        name=copy.name[:150],
        xero_contact_id=copy.xero_guid,
        xero_org_id=str(entity.xero_org_id),
        xero_contact_link_id=copy.id,
    )
    logger.info("Supplier %s brought back from Xero's archive for company %s", row.id, entity.id)
    return row


def create_entity_bill_contact_in_xero(
    entity_id: str,
    *,
    jwt_user_id: str,
    name: str,
) -> dict:
    """The company's supplier of ``name`` (owner, 2026-11-12: never blocked behind Xero, never a
    duplicate): the one it has; else, live on Xero, Xero's of that name or a new one made there;
    else its own Minty row. Returns the list items' shape (``EntityBillContactOut``)."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise BillValidationError("I'll need a contact name here.")
    if len(cleaned) > 100:  # Bill.contact_name is CharField(max_length=100)
        raise BillValidationError("Keep the supplier's name to 100 characters or fewer.")

    entity = Entity.objects.filter(id=entity_id).first()
    if not entity:
        raise BillValidationError("I couldn't find that company.")
    held = held_contact(entity_id, cleaned)
    if held is not None:
        return _db_to_bill_contact(held)
    offline = minty_on_its_own(entity_id) or entity.status != "connected" or not entity.xero_org_id

    def token_for() -> str:
        access_token = resolve_xero_access_token_for_entity(entity_id, jwt_user_id)
        if not access_token:
            raise BillValidationError("The Xero connection needs reconnecting before I can do that.")
        return access_token

    back = _brought_back(entity, cleaned, offline, token_for)
    if back is not None:
        return _db_to_bill_contact(back)
    if offline:
        return _kept_in_minty(entity_id, cleaned)

    access_token = token_for()
    xero_org_id = str(entity.xero_org_id)
    contact_id, contact_name = xero_contact_for(entity, access_token, cleaned)

    row = XeroContactSync.objects.filter(entity_id=entity_id, xero_contact_id=contact_id).first()
    if row is None:
        try:
            with transaction.atomic():
                row = XeroContactSync.objects.create(
                    id=str(uuid.uuid4()),
                    entity_id=entity_id,
                    xero_contact_id=contact_id,
                    xero_org_id=xero_org_id,
                    name=contact_name,
                    category=None,
                )
        except IntegrityError:
            # the sync stored the same contact since the look-up (uq_xcs_entity_contact,
            # 2026-11-03): that row is this contact.
            row = XeroContactSync.objects.get(entity_id=entity_id, xero_contact_id=contact_id)
    if (row.name, row.xero_org_id) != (contact_name, xero_org_id):
        row.name = contact_name
        row.xero_org_id = xero_org_id
        row.save(update_fields=["name", "xero_org_id"])

    return _db_to_bill_contact(row)
