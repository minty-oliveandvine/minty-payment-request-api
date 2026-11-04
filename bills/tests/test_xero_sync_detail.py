"""GET /bills/{id}/xero-syncs and /bills/{id}/xero-syncs/{sync_id}.

2026-11-03: the detail of any sync that had lines failed with a 500 - it read
``bill_line_item_id`` from a sync line, whose field is ``bill_line``. No test read a sync with
lines until now.
"""

import pytest

from bills.models import Bill, BillLineItem, XeroBillSync, XeroBillSyncLine


@pytest.fixture
def synced(db, test_user, test_entity):
    bill = Bill.objects.create(
        entity_id=test_entity.id,
        contact="Vendor A",
        status="submitted",
        amount=100,
        description="Printer paper",
        uploaded_by=test_user.id,
    )
    line = BillLineItem.objects.create(
        bill=bill, description="Printer paper", quantity=1, unit_amount=100, line_amount=100,
        account_code="429",
    )
    sync = XeroBillSync.objects.create(bill=bill, sync_direction="push", sync_type="create_invoice")
    XeroBillSyncLine.objects.create(
        xero_bill_sync=sync, bill_line=line, description="Printer paper", quantity=1,
        unit_amount=100, line_amount=100, account_code="429",
    )
    return bill, line, sync


@pytest.mark.django_db
def test_a_sync_with_lines_reads_back_with_them(
    api_client, auth_headers, test_user_entity, synced
):
    bill, line, sync = synced

    resp = api_client.get(f"/api/v1/bills/{bill.id}/xero-syncs/{sync.id}", **auth_headers)

    assert resp.status_code == 200, resp.content
    [sent] = resp.json()["sync_lines"]
    assert sent["bill_line_item_id"] == str(line.id)
    assert sent["description"] == "Printer paper"


@pytest.mark.django_db
def test_a_sync_line_whose_bill_line_is_gone_reads_none(
    api_client, auth_headers, test_user_entity, synced
):
    bill, line, sync = synced
    line.delete()  # SET_NULL: the sync keeps what was sent

    resp = api_client.get(f"/api/v1/bills/{bill.id}/xero-syncs/{sync.id}", **auth_headers)

    assert resp.status_code == 200, resp.content
    assert resp.json()["sync_lines"][0]["bill_line_item_id"] is None


@pytest.mark.django_db
def test_the_list_names_the_sync(api_client, auth_headers, test_user_entity, synced):
    bill, _line, sync = synced

    resp = api_client.get(f"/api/v1/bills/{bill.id}/xero-syncs", **auth_headers)

    assert resp.status_code == 200, resp.content
    assert [s["id"] for s in resp.json()] == [str(sync.id)]
