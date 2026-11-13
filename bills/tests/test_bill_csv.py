"""The bills CSV (``POST /bills/export.csv``, ``services/bill_csv.py``; owner, 2026-11-13): the
bills the list shows - ticked, or every bill the filters leave - one line per bill."""

import csv
import io
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from minty_db.models import Entity, UserEntity

from bills.models import Bill, BillLine, Payment

pytestmark = pytest.mark.django_db

URL = "/api/v1/bills/export.csv"
HEADER = ["Payment No.", "Supplier", "Invoice Date", "Due Date", "Submitted Date", "Status", "Description",
          "Account Code", "Amount", "Unpaid Amount", "Paid Date", "Currency"]


def _bill(entity, ref, status, amount, invoice_date, **fields):
    bill = Bill.objects.create(entity_id=entity.id, contact=f"{ref} Supplier", status=status,
                               amount=Decimal(str(amount)), reference=ref, invoice_date=invoice_date,
                               currency_code="HKD", **fields)
    Bill.objects.filter(id=bill.id).update(created_at=datetime(2026, 11, 1, 2, tzinfo=UTC))
    return bill


@pytest.fixture
def bills(test_user, test_entity, test_user_entity):
    paid = _bill(test_entity, "MBI-1", "partially_paid", "300", date(2026, 10, 5),
                 due_date=date(2026, 10, 20), description="Rent, October", xero_account_code="999")
    BillLine.objects.create(bill=paid, description="Rent", account_code="469", line_amount=250, sort_order=0)
    BillLine.objects.create(bill=paid, description="Fees", account_code="404", line_amount=40, sort_order=1)
    BillLine.objects.create(bill=paid, description="More rent", account_code="469", line_amount=10, sort_order=2)
    Payment.objects.create(bill=paid, payment_date="2026-10-21", amount=Decimal("100.5"), currency_code="HKD",
                           payment_status=Payment.PaymentStatus.COMPLETED, created_by=test_user.id)
    void = _bill(test_entity, "MBI-2", "void", "80", date(2026, 10, 7), xero_account_code="429")
    draft = _bill(test_entity, "MBI-3", "draft", "15", date(2026, 10, 6))
    later = _bill(test_entity, "MBI-4", "submitted", "12.5", date(2026, 12, 1))
    return {"paid": paid, "void": void, "draft": draft, "later": later}


PAID_ROW = ["MBI-1", "MBI-1 Supplier", "2026-10-05", "2026-10-20", "2026-11-01", "Partially Paid",
            "Rent, October", "469; 404", "300.00", "199.50", "2026-10-21", "HKD"]
VOID_ROW = ["MBI-2", "MBI-2 Supplier", "2026-10-07", "", "2026-11-01", "Voided", "", "429", "80.00", "0.00", "",
            "HKD"]
DRAFT_ROW = ["MBI-3", "MBI-3 Supplier", "2026-10-06", "", "2026-11-01", "Draft", "", "", "15.00", "15.00", "",
             "HKD"]


def _post(client, headers, **body):
    return client.post(URL, data=json.dumps(body), content_type="application/json", **headers)


def _table(resp):
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8"))))


def test_the_bills_any_status_by_invoice_date(api_client, auth_headers, bills):
    ids = [str(bills[k].id) for k in ("void", "paid", "draft")]
    resp = _post(api_client, auth_headers, ids=ids)

    assert resp.status_code == 200, resp.content[:300]
    assert _table(resp) == [HEADER, PAID_ROW, DRAFT_ROW, VOID_ROW]
    assert resp["Content-Type"] == "text/csv; charset=utf-8"
    assert re.fullmatch(r"attachment; filename=bills_\d{4}-\d{2}-\d{2}\.csv", resp["Content-Disposition"])
    assert resp.content.endswith(b"\n") and not resp.content.startswith(b"\xef\xbb\xbf")


def test_another_companys_bill_is_refused_whole(api_client, auth_headers, bills, test_user):
    theirs = Entity.objects.create(id=uuid.uuid4(), name="Theirs", country_code="HK",
                                   currency_id="11111111-1111-1111-1111-111111111111", status="disconnected")
    UserEntity.objects.create(user=test_user, entity=theirs, role="admin", approved=True)
    other = _bill(theirs, "THEIRS-1", "submitted", "1", date(2026, 10, 5))

    for ids in ([str(bills["paid"].id), str(other.id)], ["not-a-uuid"], [str(uuid.uuid4())]):
        resp = _post(api_client, auth_headers, ids=ids)
        assert resp.status_code == 404, ids
        assert resp.json() == {"detail": "One of the bills isn't in this company any more. "
                                         "Refresh the list and try again."}


@pytest.mark.parametrize("body, message", [
    ({"ids": []}, "There are no bills to download. Tick some bills, or change the filters."),
    ({"ids": [str(uuid.uuid4()) for _ in range(2001)]},
     "That's more than 2,000 bills. Narrow the filters to 2,000 or fewer."),
])
def test_a_bad_request_is_refused(api_client, auth_headers, bills, body, message):
    resp = _post(api_client, auth_headers, **body)
    assert resp.status_code == 422 and resp.json() == {"detail": message}


def test_the_queries_do_not_grow_with_the_bills(api_client, auth_headers, bills, test_entity):
    def count():
        with CaptureQueriesContext(connection) as seen:
            assert _post(api_client, auth_headers, ids=ids).status_code == 200
        return len(seen)

    ids = [str(b.id) for b in bills.values()]
    before = count()
    for n in range(5):
        extra = _bill(test_entity, f"MBI-X{n}", "submitted", "1", date(2026, 10, 9))
        BillLine.objects.create(bill=extra, description="x", account_code="400", line_amount=1)
        ids.append(str(extra.id))
    assert count() == before


def test_a_stranger_is_refused_at_the_door(api_client, bills):
    resp = _post(api_client, {}, ids=[str(bills["paid"].id)])
    assert resp.status_code == 401
