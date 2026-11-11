"""Who keeps the books (Xero optional, minty-db v0.5's EXPAND, 2026-11-10; Minty
docs/modernisation/xero_optional_database.md). A company that chose "Use Minty independently" in
onboarding has ``entities.setup_mode = 'minty'`` and no Xero organisation: its suppliers are its own,
and its bills are never published. Connected later, Xero's rules apply again."""

from minty_db.models import Entity

NOT_IN_XERO = "This company keeps its books in Minty and isn't connected to Xero, so there's nowhere to publish to."
#: Connecting Xero later (Settings Phase 2, 2026-11-11): signed in, the accounts not matched yet.
STILL_MATCHING = "Finish matching your accounts to Xero first (Settings, under Integration), then publish."
#: Finish set ``entities.xero_publish_from`` (owner: only records from then on go to Xero).
BEFORE_XERO = "This payment request is from before Xero was connected, so it stays in Minty."


def minty_on_its_own(entity_id) -> bool:
    row = Entity.objects.filter(id=entity_id).values_list("setup_mode", "xero_org_id").first()
    return row is not None and row[0] == "minty" and not row[1]


def publish_refusal(entity_id, bill) -> str | None:
    """Why this bill can't go to Xero, from who keeps the books - or None. A company still in Minty
    (no organisation, or one recorded but its accounts not matched yet), and a bill dated before
    Xero was connected (its invoice date, else the day it was made), stay in Minty."""
    entity = Entity.objects.filter(id=entity_id).values("setup_mode", "xero_org_id", "xero_publish_from").first()
    if entity is None:
        return None
    if entity["setup_mode"] == "minty":
        return STILL_MATCHING if entity["xero_org_id"] else NOT_IN_XERO
    cut_off = entity["xero_publish_from"]
    dated = bill.invoice_date or (bill.created_at.date() if bill.created_at else None)
    if cut_off and dated and dated < cut_off:
        return BEFORE_XERO
    return None
