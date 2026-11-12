"""Xero optional (minty-db v0.5's EXPAND, 2026-11-10; Minty docs/modernisation/xero_optional_database.md).

- A bill line names its account by id (``bill_line.account_id``) as well as by code: the company's
  active account with that code - a Xero one, or one it made in Minty.
- The publish writes what it sent: ``published_account_code``/``name`` on each line and
  ``published_contact_name`` on the bill (``xero_publish_service._snapshot``).
- A company keeping its books in Minty (``setup_mode = 'minty'``, no Xero org): its suppliers are
  Minty's own rows, named by their row id where Xero's id is missing; adding one makes no Xero call;
  publishing is refused before Xero is asked.
"""

from unittest.mock import patch

import pytest
from minty_db.models import AccountInfo, Entity, XeroContactSync

from bills.models import Bill, BillLine
from bills.services import books
from bills.services.xero_publish_service import _snapshot


def _account(entity, code, name, *, xero_id=None, archived=False):
    return AccountInfo.objects.create(
        entity_id=entity.id, name=name, type="EXPENSE", xero_code=code, code=code, account_type="expense",
        xero_account_id=xero_id, status="ARCHIVED" if archived else "ACTIVE",
        record_status="archived" if archived else "active", source="xero" if xero_id else "user")


@pytest.fixture
def minty(test_entity):
    Entity.objects.filter(id=test_entity.id).update(setup_mode="minty", xero_org_id=None)
    return test_entity


@pytest.mark.django_db
def test_a_line_names_its_account_by_id(draft_bill, test_entity):
    rent = _account(test_entity, "469", "Rent", xero_id="g-469")
    _account(test_entity, "469", "Old Rent", archived=True)
    line = BillLine.objects.create(bill=draft_bill, description="May rent", account_code="469", line_amount=100)
    assert line.account_id == rent.id

    own = _account(test_entity, "405", "Cleaning")
    line.account_code = "405"
    line.save()
    assert BillLine.objects.get(id=line.id).account_id == own.id, "a Minty-made account too"

    line.account_code = ""
    line.save()
    assert BillLine.objects.get(id=line.id).account_id is None


@pytest.mark.django_db
def test_an_unknown_code_leaves_no_account(draft_bill):
    line = BillLine.objects.create(bill=draft_bill, description="x", account_code="999", line_amount=1)
    assert line.account_id is None


@pytest.mark.django_db
def test_the_publish_snapshot_is_what_was_sent(draft_bill):
    BillLine.objects.create(bill=draft_bill, description="x", account_code="469", account_name="Rent",
                            line_amount=100)
    Bill.objects.filter(id=draft_bill.id).update(contact_name="Corner Bakery")
    bill = Bill.objects.get(id=draft_bill.id)

    _snapshot(bill)

    bill = Bill.objects.get(id=draft_bill.id)
    assert bill.published_contact_name == "Corner Bakery"
    line = bill.line_items.get()
    assert (line.published_account_code, line.published_account_name) == ("469", "Rent")


@pytest.mark.django_db
def test_a_supplier_is_kept_in_minty_with_no_xero_call(api_client, auth_headers, test_user_entity, minty):
    with patch("requests.post", side_effect=AssertionError("no Xero call")):
        resp = api_client.post("/api/v1/entity-bill-contacts/", data={"name": " Corner  Bakery "},
                               content_type="application/json", **auth_headers)
    assert resp.status_code == 201
    row = XeroContactSync.objects.get(entity_id=minty.id)
    assert (row.name, row.xero_contact_id, row.source) == ("Corner Bakery", None, "user")
    assert resp.json()["id"] == str(row.id)

    again = api_client.post("/api/v1/entity-bill-contacts/", data={"name": "corner bakery"},
                            content_type="application/json", **auth_headers)
    assert again.status_code == 201 and again.json()["id"] == str(row.id)
    listed = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers).json()
    assert [(c["id"], c["name"]) for c in listed] == [(str(row.id), "Corner Bakery")]


@pytest.mark.django_db
def test_a_bill_takes_a_minty_supplier_by_its_row_id(draft_bill, minty):
    row = XeroContactSync.objects.create(entity_id=minty.id, name="Corner Bakery", source="user")
    bill = Bill.objects.get(id=draft_bill.id)
    bill.xero_contact_id = str(row.id)
    bill.save()
    bill = Bill.objects.get(id=draft_bill.id)
    assert bill.contact_id == row.id
    assert bill.xero_contact_id == str(row.id)
    assert XeroContactSync.objects.filter(entity_id=minty.id).count() == 1, "no Xero-keyed row made"


@pytest.mark.django_db
def test_publishing_is_refused_before_xero_is_asked(api_client, auth_headers, test_user_entity, draft_bill, minty):
    with patch("bills.api.resolve_xero_access_token_for_entity", side_effect=AssertionError("asked Xero")):
        resp = api_client.post(f"/api/v1/bills/{draft_bill.id}/publish/", **auth_headers)
    assert resp.status_code == 422
    assert resp.json() == {"detail": books.NOT_IN_XERO}


@pytest.mark.django_db
def test_the_rule_reads_setup_mode_and_the_org(test_entity):
    assert not books.minty_on_its_own(test_entity.id)
    Entity.objects.filter(id=test_entity.id).update(setup_mode="minty")
    assert books.minty_on_its_own(test_entity.id)
    Entity.objects.filter(id=test_entity.id).update(xero_org_id="org-1")
    assert books.minty_on_its_own(test_entity.id), "an org recorded mid-matching: still Minty's (2026-11-11)"
    Entity.objects.filter(id=test_entity.id).update(setup_mode="xero")
    assert not books.minty_on_its_own(test_entity.id), "Finish hands it to Xero's rules"


@pytest.mark.django_db
def test_the_status_says_the_company_keeps_its_books_in_minty(api_client, auth_headers, test_user_entity, test_entity):
    assert api_client.get("/api/v1/auth/xero-status", **auth_headers).json() == {"connected": False,
                                                                                   "minty_only": False}
    Entity.objects.filter(id=test_entity.id).update(setup_mode="minty", xero_org_id=None)
    assert api_client.get("/api/v1/auth/xero-status", **auth_headers).json()["minty_only"] is True


# ---- connecting Xero later (Settings Phase 2, 2026-11-11) ------------------------------------------


@pytest.mark.django_db
def test_publishing_waits_while_the_accounts_are_being_matched(api_client, auth_headers, test_user_entity,
                                                               draft_bill, minty):
    Entity.objects.filter(id=minty.id).update(xero_org_id="org-1", status="disconnected")
    with patch("bills.api.resolve_xero_access_token_for_entity", side_effect=AssertionError("asked Xero")):
        resp = api_client.post(f"/api/v1/bills/{draft_bill.id}/publish/", **auth_headers)
    assert (resp.status_code, resp.json()) == (422, {"detail": books.STILL_MATCHING})


@pytest.mark.django_db
def test_a_bill_from_before_xero_was_connected_stays_in_minty(api_client, auth_headers, test_user_entity,
                                                              draft_bill, minty):
    """Finish sets xero_publish_from (owner: only records from then on go to Xero)."""
    import datetime as dt

    Entity.objects.filter(id=minty.id).update(setup_mode="xero", xero_org_id="org-1", status="connected",
                                              xero_publish_from=dt.date(2026, 11, 11))
    type(draft_bill).objects.filter(id=draft_bill.id).update(invoice_date=dt.date(2026, 11, 10))
    with patch("bills.api.resolve_xero_access_token_for_entity", side_effect=AssertionError("asked Xero")):
        resp = api_client.post(f"/api/v1/bills/{draft_bill.id}/publish/", **auth_headers)
    assert (resp.status_code, resp.json()) == (422, {"detail": books.BEFORE_XERO})


# ---- Phase 3 of the Xero matching (owner, 2026-11-12): a stale match is refused, named ----------------


def _connected(entity):
    Entity.objects.filter(id=entity.id).update(setup_mode="xero", xero_org_id="org-1", status="connected")


def _copy(entity, guid, org="org-1"):
    from django.utils import timezone
    from minty_db.models import XeroAccount

    XeroAccount.objects.create(entity_id=entity.id, xero_org_id=org, xero_guid=guid, name=guid, xero_type="EXPENSE",
                               status="ACTIVE", synced_at=timezone.now())


def _publish(api_client, auth_headers, bill):
    with patch("bills.api.resolve_xero_access_token_for_entity", side_effect=AssertionError("asked Xero")):
        return api_client.post(f"/api/v1/bills/{bill.id}/publish/", **auth_headers)


@pytest.mark.django_db
def test_a_line_on_an_account_of_another_organisation_is_refused_by_name(api_client, auth_headers, test_user_entity,
                                                                          draft_bill, test_entity):
    _connected(test_entity)
    _copy(test_entity, "g-405")
    _account(test_entity, "469", "Rent", xero_id="old-469")
    _account(test_entity, "405", "Cleaning", xero_id="g-405")
    BillLine.objects.create(bill=draft_bill, description="May rent", account_code="469", line_amount=60)
    BillLine.objects.create(bill=draft_bill, description="Cleaning", account_code="405", line_amount=40)

    resp = _publish(api_client, auth_headers, draft_bill)

    assert (resp.status_code, resp.json()) == (422, {"detail": "Rent (469) isn't matched to your Xero organisation. "
                                                               "Match it in Settings, under Integration, then "
                                                               "publish again."})


@pytest.mark.django_db
def test_a_line_on_an_account_archived_in_xero_is_refused_by_name(api_client, auth_headers, test_user_entity,
                                                                  draft_bill, test_entity):
    from minty_db.models import XeroAccount

    _connected(test_entity)
    _copy(test_entity, "g-469")
    XeroAccount.objects.filter(entity_id=test_entity.id, xero_guid="g-469").update(status="ARCHIVED")
    _account(test_entity, "469", "Rent", xero_id="g-469")
    BillLine.objects.create(bill=draft_bill, description="May rent", account_code="469", line_amount=100)

    resp = _publish(api_client, auth_headers, draft_bill)

    assert (resp.status_code, resp.json()) == (422, {"detail": "Rent (469) is archived in Xero. Restore it in Xero "
                                                               "or choose another account, then publish again."})


@pytest.mark.django_db
def test_a_supplier_of_an_older_organisation_is_not_refused(test_entity, draft_bill):
    """The census (production's data, 2026-11-12): a bill whose last publish went to another
    organisation is published fresh into the current one, as it always has."""
    _connected(test_entity)
    vendor = XeroContactSync.objects.create(entity_id=test_entity.id, name="Vendor A", xero_contact_id="c-a",
                                            xero_org_id="org-old")
    type(draft_bill).objects.filter(id=draft_bill.id).update(contact_id=vendor.id)
    draft_bill.refresh_from_db()
    assert books.publish_refusal(test_entity.id, draft_bill) is None


@pytest.mark.django_db
@pytest.mark.parametrize("reason", ["first", "new_accounts", "new_org"])
def test_an_open_matching_holds_every_publish(api_client, auth_headers, test_user_entity, draft_bill, test_entity,
                                              reason):
    _connected(test_entity)
    Entity.objects.filter(id=test_entity.id).update(xero_matching=reason)
    resp = _publish(api_client, auth_headers, draft_bill)
    assert (resp.status_code, resp.json()) == (422, {"detail": books.STILL_MATCHING})


@pytest.mark.django_db
def test_a_change_of_matches_in_progress_does_not_hold_a_publish(test_entity, draft_bill):
    _connected(test_entity)
    Entity.objects.filter(id=test_entity.id).update(xero_matching="change")
    assert books.publish_refusal(test_entity.id, draft_bill) is None

