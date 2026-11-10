"""Who keeps the books (Xero optional, minty-db v0.5's EXPAND, 2026-11-10; Minty
docs/modernisation/xero_optional_database.md). A company that chose "Use Minty independently" in
onboarding has ``entities.setup_mode = 'minty'`` and no Xero organisation: its suppliers are its own,
and its bills are never published. Connected later, Xero's rules apply again."""

from minty_db.models import Entity

NOT_IN_XERO = "This company keeps its books in Minty and isn't connected to Xero, so there's nowhere to publish to."


def minty_on_its_own(entity_id) -> bool:
    row = Entity.objects.filter(id=entity_id).values_list("setup_mode", "xero_org_id").first()
    return row is not None and row[0] == "minty" and not row[1]
