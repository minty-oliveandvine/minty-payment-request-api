"""Auto-generate unique bill references: MBI + 3 company-name letters + HHMMSS + DDMMYY (Asia/Hong_Kong).

The letters are the company's (owner, 2026-11-03); they used to be the signed-in person's.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from minty_db.models import Entity

from bills.models import Bill

logger = logging.getLogger("minty-api")

HK_TZ = ZoneInfo("Asia/Hong_Kong")

_STATUSES_EXCLUDED_FROM_UNIQUENESS = (Bill.Status.VOID,)


def _name_prefix_3(company_name: str) -> str:
    """The first 3 letters of the company's name (letters only, A-Z), padded with X."""
    letters = "".join(c for c in (company_name or "") if c.isascii() and c.isalpha())
    return (letters + "XXX")[:3].upper()


def _company_name(entity_id: str) -> str:
    return Entity.objects.only("name").get(id=entity_id).name or ""


def _reference_taken(entity_id: str, ref: str) -> bool:
    return (
        Bill.objects.filter(entity_id=entity_id)
        .exclude(status__in=_STATUSES_EXCLUDED_FROM_UNIQUENESS)
        .filter(reference__iexact=ref)
        .exists()
    )


def build_bill_reference(company_name: str, at: datetime) -> str:
    """MBI + 3 company-name letters + '-' + HHMMSS + DDMMYY in Hong Kong time."""
    t = at.astimezone(HK_TZ)
    stamp = f"{t.strftime('%H%M%S')}{t.strftime('%d%m%y')}"
    return f"MBI{_name_prefix_3(company_name)}-{stamp}"


def generate_unique_bill_reference(entity_id: str) -> str:
    """Return a reference not used by any non-voided/non-cancelled bill in the entity."""
    company_name = _company_name(entity_id)
    for attempt in range(64):
        at = datetime.now(HK_TZ)
        base = build_bill_reference(company_name, at)
        ref = base if attempt == 0 else f"{base}-{secrets.token_hex(2).upper()}"
        if not _reference_taken(entity_id, ref):
            if attempt:
                logger.info(
                    "Bill reference collision resolved with suffix entity_id=%s ref=%s",
                    entity_id,
                    ref,
                )
            return ref
    logger.error(
        "Could not allocate unique bill reference after 64 attempts entity_id=%s",
        entity_id,
    )
    return f"{build_bill_reference(company_name, datetime.now(HK_TZ))}-{secrets.token_hex(4).upper()}"
