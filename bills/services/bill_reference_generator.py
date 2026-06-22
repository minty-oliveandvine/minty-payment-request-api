"""Auto-generate unique bill references: MBI + 3 name letters + HHMMSS + DDMMYY (Asia/Hong_Kong)."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from bills.models import Bill
from shared_models.models import User

logger = logging.getLogger("minty-api")

HK_TZ = ZoneInfo("Asia/Hong_Kong")

_STATUSES_EXCLUDED_FROM_UNIQUENESS = (
    Bill.Status.VOIDED,
    Bill.Status.CANCELLED,
)


def _name_prefix_3(user: User) -> str:
    """First 3 letters from first name (letters only); pad from last name, username, or X."""
    first = "".join(c for c in (user.first_name or "").strip() if c.isalpha())
    if len(first) >= 3:
        return first[:3].upper()
    rest = "".join(c for c in (user.last_name or "").strip() if c.isalpha())
    combined = (first + rest)[:3]
    if len(combined) < 3:
        extra = "".join(c for c in (user.username or user.email or "") if c.isalpha())
        combined = (combined + extra)[:3]
    if len(combined) < 3:
        combined = (combined + "XXX")[:3]
    return combined.upper()


def _reference_taken(entity_id: str, ref: str) -> bool:
    return (
        Bill.objects.filter(entity_id=entity_id)
        .exclude(status__in=_STATUSES_EXCLUDED_FROM_UNIQUENESS)
        .filter(reference__iexact=ref)
        .exists()
    )


def build_bill_reference(user: User, at: datetime) -> str:
    """MBI + 3 name letters + '-' + HHMMSS + DDMMYY in Hong Kong time."""
    t = at.astimezone(HK_TZ)
    stamp = f"{t.strftime('%H%M%S')}{t.strftime('%d%m%y')}"
    return f"MBI{_name_prefix_3(user)}-{stamp}"


def generate_unique_bill_reference(entity_id: str, user: User) -> str:
    """Return a reference not used by any non-voided/non-cancelled bill in the entity."""
    for attempt in range(64):
        at = datetime.now(HK_TZ)
        base = build_bill_reference(user, at)
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
    return f"{build_bill_reference(user, datetime.now(HK_TZ))}-{secrets.token_hex(4).upper()}"
