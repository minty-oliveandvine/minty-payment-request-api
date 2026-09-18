"""Characterisation: one bill's life through the API, pinned on the rows it leaves (C8).

    create with lines      -> the bill and its lines; the detail shows contact, creator, lines
    submit                 -> status `submitted`, an audit row
    return, pay            -> `returned`; a payment moves the bill to `partially_paid` / `paid`
    attach                 -> attachment rows on the bill and on the payment (S3 stubbed)
    publish                -> a `xero_bill_sync` row for the push (Xero stubbed at `requests`),
                              `published = published`
    void                   -> `void`, the reference reusable

Written 2026-09-17 before the C8 model changes (`bill_line_item -> bill_line`,
`audit -> bill_audit`, `bill.contact / xero_contact_id / currency_code / uploaded_by ->
contact_name / contact_id / currency_id / created_by`, the `bill_status` / `publish_state`
enums with `void` and `draft`). The API contract the frontend sees is what is pinned here;
the rows are read back through the models by their table, not by column name where the
column is what changes.
"""

from __future__ import annotations

import datetime
import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from bills.models import Bill, Payment
from shared_models.models import Entity, User, UserEntity, XeroContactSync

pytestmark = pytest.mark.django_db

ORG = "org-char-0001"
XERO_CONTACT = "c0ffee00-0000-4000-8000-000000000001"


@pytest.fixture
def company(db):
    return Entity.objects.create(
        id="9df620d9-a0f3-5f42-a200-04f17c73b0c8", name="Char Co", country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111", status="connected", xero_org_id=ORG,
    )


@pytest.fixture
def clerk(db, company):
    user = User.objects.create(
        id="96dc83ad-f6b8-5b7d-aa0a-26f5f76f80c8", email="clerk@minty.com", password="x",
        first_name="Casey", last_name="Clerk", username="clerk", system_role="normal",
    )
    UserEntity.objects.create(user=user, entity=company, role="admin")
    XeroContactSync.objects.create(
        id=uuid.uuid5(uuid.NAMESPACE_URL, "char-contact"), entity_id=company.id,
        xero_contact_id=XERO_CONTACT, xero_org_id=ORG, name="Acme Supplies",
    )
    return user


@pytest.fixture
def headers(clerk, company):
    import jwt
    from django.conf import settings

    token = jwt.encode({"user_id": clerk.id, "entity_id": company.id}, settings.SECRET_KEY, algorithm="HS256")
    return {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_ENTITY_ID": company.id}


def fake_s3():
    """An S3 client that stores nothing and signs a URL - the response schema wants a string."""
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://s3.test/signed"
    return client


def post(api_client, url, body, headers):
    return api_client.post(url, data=json.dumps(body), content_type="application/json", **headers)


def put(api_client, url, body, headers):
    return api_client.put(url, data=json.dumps(body), content_type="application/json", **headers)


LINES = [
    {"description": "Toner", "quantity": "2", "unit_amount": "75.00", "line_amount": "150.00", "account_code": "400"},
    {"description": "Paper", "quantity": "1", "unit_amount": "100.10", "line_amount": "100.10", "account_code": "400"},
]


def create_bill(api_client, headers, **overrides):
    body = {"contact": "Acme Supplies", "xero_contact_id": XERO_CONTACT, "currency_code": "HKD",
            "amount": "250.10", "reference": "INV-CHAR-1", "description": "Consumables",
            "invoice_date": "2026-09-01", "due_date": "2026-09-30", "line_items": LINES}
    body.update(overrides)
    resp = post(api_client, "/api/v1/bills/", body, headers)
    assert resp.status_code == 201, resp.content[:300]
    return resp.json()


def audit_trail(bill_id) -> list[str]:
    """The actions on the bill's audit rows, oldest first (the table name is what changes)."""
    from bills import models as m

    model = getattr(m, "BillAudit", None) or getattr(m, "Audit")
    return [row.action for row in model.objects.filter(bill_id=bill_id).order_by("created_at" if hasattr(model, "created_at") else "date")]


# ---- create / detail ------------------------------------------------------------------------


def test_a_bill_with_lines_is_stored_with_its_lines_and_shown_with_its_people(api_client, headers, clerk, company):
    body = create_bill(api_client, headers)

    assert body["status"] == "draft"
    assert body["contact"] == "Acme Supplies" and body["xero_contact_id"] == XERO_CONTACT
    assert body["currency_code"] == "HKD"
    assert Decimal(str(body["amount"])) == Decimal("250.10")
    assert [l["description"] for l in body["line_items"]] == ["Toner", "Paper"]
    assert sum(Decimal(str(l["line_amount"])) for l in body["line_items"]) == Decimal("250.10")

    detail = api_client.get(f"/api/v1/bills/{body['id']}", **headers)
    assert detail.status_code == 200, detail.content[:300]
    d = detail.json()
    assert d["contact"] == "Acme Supplies"
    assert len(d["line_items"]) == 2
    assert d.get("uploaded_by") in (clerk.id, str(clerk.id)) or d.get("created_by") in (clerk.id, str(clerk.id))

    bill = Bill.objects.get(id=body["id"])
    assert bill.line_items.count() == 2
    assert str(bill.entity_id) == company.id
    assert "created" in audit_trail(bill.id)


# ---- the status machine -------------------------------------------------------------------------


def test_submit_return_and_payments_move_the_status_and_leave_a_trail(api_client, headers, clerk, company):
    bill_id = create_bill(api_client, headers)["id"]

    submitted = put(api_client, f"/api/v1/bills/{bill_id}", {"status": "submitted"}, headers)
    assert submitted.status_code == 200, submitted.content[:300]
    assert submitted.json()["status"] == "submitted"

    # "payment_requested" is the return action's name on the wire: submitted -> returned
    returned = post(api_client, f"/api/v1/bills/{bill_id}/return/", {"status": "payment_requested"}, headers)
    assert returned.status_code in (200, 201), returned.content[:300]
    assert Bill.objects.get(id=bill_id).status == "returned"

    put(api_client, f"/api/v1/bills/{bill_id}", {"status": "submitted"}, headers)

    first = post(api_client, f"/api/v1/bills/{bill_id}/payments",
                 {"payment_date": "2026-09-10", "amount": "100.10", "payment_status": "completed",
                  "payment_method": "bank_transfer", "reference_no": "PAY-1"}, headers)
    assert first.status_code == 201, first.content[:300]
    assert Bill.objects.get(id=bill_id).status == "partially_paid"

    second = post(api_client, f"/api/v1/bills/{bill_id}/payments",
                  {"payment_date": "2026-09-11", "amount": "150.00", "payment_status": "completed",
                   "payment_method": "bank_transfer", "reference_no": "PAY-2"}, headers)
    assert second.status_code == 201, second.content[:300]
    bill = Bill.objects.get(id=bill_id)
    assert bill.status == "paid"
    assert sum(p.amount for p in Payment.objects.filter(bill_id=bill_id)) == Decimal("250.10")

    trail = audit_trail(bill_id)
    assert trail[0] == "created"
    assert "payment_created" in trail
    assert len(trail) >= 4


def test_voiding_frees_the_reference(api_client, headers, clerk, company):
    bill_id = create_bill(api_client, headers)["id"]
    put(api_client, f"/api/v1/bills/{bill_id}", {"status": "submitted"}, headers)

    gone = api_client.delete(f"/api/v1/bills/{bill_id}", **headers)

    assert gone.status_code == 200, gone.content[:300]
    bill = Bill.objects.get(id=bill_id)
    assert bill.status in ("void", "void")  # the enum word is `void` after C8
    listed = api_client.get("/api/v1/bills/?status=" + bill.status, **headers)
    assert listed.status_code == 200
    again = create_bill(api_client, headers)  # same reference, accepted
    assert again["reference"] == "INV-CHAR-1"


# ---- attachments ---------------------------------------------------------------------------------


def test_attachments_hang_off_the_bill_and_the_payment(api_client, headers, clerk, company):
    bill_id = create_bill(api_client, headers)["id"]
    invoice = SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 fake", content_type="application/pdf")

    with patch("bills.services.attachment_service._get_s3_client", return_value=fake_s3()):
        up = api_client.post(f"/api/v1/bills/{bill_id}/attachments", data={"files": invoice}, **headers)
    assert up.status_code == 201, up.content[:300]
    (row,) = up.json()
    assert row["attachment"]["original_name"] == "invoice.pdf"
    assert row["attachment_role"] in ("invoice", "other", "supporting_document")

    listed = api_client.get(f"/api/v1/bills/{bill_id}/attachments", **headers)
    assert listed.status_code == 200 and len(listed.json()) == 1

    put(api_client, f"/api/v1/bills/{bill_id}", {"status": "submitted"}, headers)
    pay = post(api_client, f"/api/v1/bills/{bill_id}/payments",
               {"payment_date": "2026-09-10", "amount": "250.10", "payment_status": "completed",
                "payment_method": "bank_transfer", "reference_no": "PAY-1"}, headers).json()
    slip = SimpleUploadedFile("slip.png", b"\x89PNG fake", content_type="image/png")
    with patch("bills.services.attachment_service._get_s3_client", return_value=fake_s3()):
        up2 = api_client.post(f"/api/v1/bills/{bill_id}/payments/{pay['id']}/attachments?attachment_role=bank_slip",
                              data={"file": slip}, **headers)
    assert up2.status_code == 201, up2.content[:300]
    row2 = up2.json()
    row2 = row2[0] if isinstance(row2, list) else row2
    assert row2["attachment"]["original_name"] == "slip.png"
    assert row2["attachment_role"] == "bank_slip"
    assert "attachment_uploaded" in audit_trail(bill_id)


# ---- publish ----------------------------------------------------------------------------------------


def _xero_200(invoice_id="xero-inv-0001"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json.return_value = {"Invoices": [{
        "InvoiceID": invoice_id, "InvoiceNumber": "INV-CHAR-1", "Status": "AUTHORISED",
        "AmountDue": 250.10, "AmountPaid": 0.0, "Total": 250.10, "CurrencyCode": "HKD",
        "LineItems": [{"LineItemID": "li-1", "Description": "Toner", "Quantity": 2.0, "UnitAmount": 75.0, "LineAmount": 150.0},
                      {"LineItemID": "li-2", "Description": "Paper", "Quantity": 1.0, "UnitAmount": 100.1, "LineAmount": 100.1}],
    }]}
    return resp


def test_publishing_records_the_push_and_marks_the_bill_published(api_client, headers, clerk, company):
    from bills.models import XeroBillSync
    from bills.services.xero_publish_service import publish_bill_to_xero

    bill_id = create_bill(api_client, headers)["id"]
    put(api_client, f"/api/v1/bills/{bill_id}", {"status": "submitted"}, headers)

    with (
        patch("bills.services.xero_publish_service.requests.put", return_value=_xero_200()) as put_,
        patch("bills.services.xero_publish_service.requests.post", return_value=_xero_200()),
        patch("bills.services.xero_publish_service.requests.get", return_value=MagicMock(status_code=404, json=lambda: {})),
        patch("bills.services.xero_publish_service._upload_bill_attachments_to_xero"),
        patch("bills.services.xero_publish_service._upload_existing_bankslips_to_xero"),
    ):
        result = publish_bill_to_xero(bill_id, company.id, clerk.id, "fake-token")

    assert result.get("success") in (True, None) or result.get("xero_invoice_id"), result
    sent = put_.call_args.kwargs.get("json") or put_.call_args.kwargs.get("data")
    if isinstance(sent, str):
        sent = json.loads(sent)
    invoice = (sent.get("Invoices") or [sent])[0]
    assert invoice["Contact"]["ContactID"] == XERO_CONTACT
    assert len(invoice["LineItems"]) == 2

    bill = Bill.objects.get(id=bill_id)
    assert bill.published == "published"
    syncs = XeroBillSync.objects.filter(bill_id=bill_id)
    assert syncs.count() >= 1
    sync = syncs.order_by("-created_at").first()
    assert sync.sync_direction in ("push", "outbound")  # `push` after C8
    assert sync.sync_status == "success"
    assert sync.response_invoice_id == "xero-inv-0001"
    assert Decimal(str(sync.response_total)) == Decimal("250.10")
    assert "published_to_xero" in audit_trail(bill_id)
