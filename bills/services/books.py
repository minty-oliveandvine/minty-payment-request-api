"""Who keeps the books (Xero optional, minty-db v0.5's EXPAND, 2026-11-10; Minty
docs/modernisation/xero_optional_database.md). A company that chose "Use Minty independently" in
onboarding has ``entities.setup_mode = 'minty'`` and no Xero organisation: its suppliers are its own,
and its bills are never published. Connected later, Xero's rules apply again."""

from minty_db.models import AccountInfo, Entity, XeroAccount

NOT_IN_XERO = "This company keeps its books in Minty and isn't connected to Xero, so there's nowhere to publish to."
#: Connecting Xero later (Settings Phase 2, 2026-11-11): signed in, the accounts not matched yet.
STILL_MATCHING = "Finish matching your accounts to Xero first (Settings, under Integration), then publish."
#: Phase 3 of the matching (owner, 2026-11-12): an open matching for these holds every publish; a
#: change of the matches (``change``) doesn't - the company stays connected meanwhile.
HOLDING_MATCHES = ("first", "new_accounts", "new_org")
#: A stale match: an account the bill uses isn't in the company's current organisation, or Xero
#: archived it. (Suppliers aren't held: the publish re-creates one Xero doesn't know, as it always has.)
STALE_ACCOUNT = ("{name} ({code}) isn't matched to your Xero organisation. Match it in Settings, under "
                 "Integration, then publish again.")
ARCHIVED_ACCOUNT = ("{name} ({code}) is archived in Xero. Restore it in Xero or choose another account, "
                    "then publish again.")
#: Finish set ``entities.xero_publish_from`` (owner: only records from then on go to Xero).
BEFORE_XERO = "This payment request is from before Xero was connected, so it stays in Minty."


def minty_on_its_own(entity_id) -> bool:
    """Minty keeps the books (``setup_mode = 'minty'``) - also while an organisation recorded
    mid-matching waits for Finish (Settings Phase 2, 2026-11-11), which sets ``'xero'``."""
    return Entity.objects.filter(id=entity_id, setup_mode="minty").exists()


def publish_refusal(entity_id, bill) -> str | None:
    """Why this bill can't go to Xero, from who keeps the books - or None. A company still in Minty
    (no organisation, or one recorded but its accounts not matched yet), and a bill dated before
    Xero was connected (its invoice date, else the day it was made), stay in Minty."""
    entity = Entity.objects.filter(id=entity_id).values(
        "setup_mode", "xero_org_id", "xero_publish_from", "xero_matching").first()
    if entity is None:
        return None
    if entity["setup_mode"] == "minty":
        return STILL_MATCHING if entity["xero_org_id"] else NOT_IN_XERO
    if entity["xero_matching"] in HOLDING_MATCHES:
        return STILL_MATCHING
    cut_off = entity["xero_publish_from"]
    dated = bill.invoice_date or (bill.created_at.date() if bill.created_at else None)
    if cut_off and dated and dated < cut_off:
        return BEFORE_XERO
    stale = stale_matches(entity_id, entity["xero_org_id"], bill)
    return "\n".join(stale) if stale else None


def stale_matches(entity_id, org, bill) -> list[str]:
    """The bill's accounts that aren't in the company's current Xero organisation, or that Xero
    archived, one sentence each (Phase 3 of the matching, owner 2026-11-12). An account is stale with
    no Xero id, or with one the organisation's copies don't hold (when the company has copies at all:
    older data without them isn't held). The supplier isn't held: a bill whose last publish went to
    another organisation is published fresh into the current one, as it always has."""
    from bills.models import BillLine

    org = str(org or "")
    account_ids = {a for a in BillLine.objects.filter(bill_id=bill.id).values_list("account_id", flat=True) if a}
    copies = dict(XeroAccount.objects.filter(entity_id=entity_id, xero_org_id=org)
                  .values_list("xero_guid", "status"))
    out = []
    for account in sorted(AccountInfo.objects.filter(id__in=account_ids), key=lambda a: (a.xero_code or "", a.name)):
        named = {"name": account.name, "code": account.xero_code or account.code or "-"}
        if not account.xero_account_id or (copies and account.xero_account_id not in copies):
            out.append(STALE_ACCOUNT.format(**named))
        elif copies and copies[account.xero_account_id] != "ACTIVE":
            out.append(ARCHIVED_ACCOUNT.format(**named))
    return out
