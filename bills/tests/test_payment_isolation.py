"""
Payment list groups by supplier (same xero_contact_id, else same contact name)
within the entity: GET /bills/{id}/payments returns payments for all matching bills.

Payments for different suppliers stay isolated. paid_total remains for the
current bill only (completed payments on that bill).
"""

import json
from decimal import Decimal

import pytest

from bills.models import Bill


@pytest.fixture
def bill_a(db, test_user, test_entity):
    return Bill.objects.create(
        entity_id=test_entity.id,
        contact="Vendor Alpha",
        status="draft",
        amount=Decimal("1000.00"),
        description="Bill A",
        uploaded_by=test_user.id,
    )


@pytest.fixture
def bill_b(db, test_user, test_entity):
    return Bill.objects.create(
        entity_id=test_entity.id,
        contact="Vendor Beta",
        status="draft",
        amount=Decimal("2000.00"),
        description="Bill B",
        uploaded_by=test_user.id,
    )


@pytest.mark.django_db
class TestPaymentIsolationDifferentSuppliers:
    """Different contact names => payment lists do not mix."""

    def test_list_payments_empty_by_default(self, api_client, auth_headers, test_user_entity, bill_a):
        resp = api_client.get(f"/api/v1/bills/{bill_a.id}/payments", **auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["payments"] == []
        assert float(body["paid_total"]) == 0

    def test_payment_only_appears_on_its_own_supplier_scope(
        self, api_client, auth_headers, test_user_entity, bill_a, bill_b
    ):
        resp_a = api_client.post(
            f"/api/v1/bills/{bill_a.id}/payments",
            data=json.dumps({
                "payment_date": "2026-03-20",
                "amount": 300,
                "payment_status": "pending",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp_a.status_code == 201
        payment_a_id = resp_a.json()["id"]

        resp_b = api_client.post(
            f"/api/v1/bills/{bill_b.id}/payments",
            data=json.dumps({
                "payment_date": "2026-03-21",
                "amount": 750,
                "payment_status": "pending",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp_b.status_code == 201
        payment_b_id = resp_b.json()["id"]

        list_a = api_client.get(f"/api/v1/bills/{bill_a.id}/payments", **auth_headers)
        assert list_a.status_code == 200
        payments_a = list_a.json()["payments"]
        assert len(payments_a) == 1
        assert payments_a[0]["id"] == payment_a_id
        assert float(payments_a[0]["amount"]) == 300

        list_b = api_client.get(f"/api/v1/bills/{bill_b.id}/payments", **auth_headers)
        assert list_b.status_code == 200
        payments_b = list_b.json()["payments"]
        assert len(payments_b) == 1
        assert payments_b[0]["id"] == payment_b_id
        assert float(payments_b[0]["amount"]) == 750

    def test_multiple_payments_on_one_bill_dont_leak(
        self, api_client, auth_headers, test_user_entity, bill_a, bill_b
    ):
        for amt in [100, 200, 150]:
            api_client.post(
                f"/api/v1/bills/{bill_a.id}/payments",
                data=json.dumps({"amount": amt, "payment_status": "pending", "payment_date": "2026-03-20"}),
                content_type="application/json",
                **auth_headers,
            )

        api_client.post(
            f"/api/v1/bills/{bill_b.id}/payments",
            data=json.dumps({"amount": 999, "payment_status": "pending", "payment_date": "2026-03-21"}),
            content_type="application/json",
            **auth_headers,
        )

        list_a = api_client.get(f"/api/v1/bills/{bill_a.id}/payments", **auth_headers)
        payments_a = list_a.json()["payments"]
        assert len(payments_a) == 3
        amounts_a = sorted([float(p["amount"]) for p in payments_a])
        assert amounts_a == [100, 150, 200]

        list_b = api_client.get(f"/api/v1/bills/{bill_b.id}/payments", **auth_headers)
        payments_b = list_b.json()["payments"]
        assert len(payments_b) == 1
        assert float(payments_b[0]["amount"]) == 999

    def test_deleting_payment_on_bill_a_doesnt_affect_bill_b(
        self, api_client, auth_headers, test_user_entity, bill_a, bill_b
    ):
        resp_a = api_client.post(
            f"/api/v1/bills/{bill_a.id}/payments",
            data=json.dumps({"amount": 500, "payment_status": "pending", "payment_date": "2026-03-20"}),
            content_type="application/json",
            **auth_headers,
        )
        payment_a_id = resp_a.json()["id"]

        api_client.post(
            f"/api/v1/bills/{bill_b.id}/payments",
            data=json.dumps({"amount": 800, "payment_status": "pending", "payment_date": "2026-03-21"}),
            content_type="application/json",
            **auth_headers,
        )

        del_resp = api_client.delete(
            f"/api/v1/bills/{bill_a.id}/payments/{payment_a_id}", **auth_headers
        )
        assert del_resp.status_code == 200

        list_a = api_client.get(f"/api/v1/bills/{bill_a.id}/payments", **auth_headers)
        assert len(list_a.json()["payments"]) == 0

        list_b = api_client.get(f"/api/v1/bills/{bill_b.id}/payments", **auth_headers)
        assert len(list_b.json()["payments"]) == 1

    def test_cannot_access_payment_from_wrong_bill(
        self, api_client, auth_headers, test_user_entity, bill_a, bill_b
    ):
        resp = api_client.post(
            f"/api/v1/bills/{bill_a.id}/payments",
            data=json.dumps({"amount": 400, "payment_status": "pending", "payment_date": "2026-03-20"}),
            content_type="application/json",
            **auth_headers,
        )
        payment_a_id = resp.json()["id"]

        get_resp = api_client.get(
            f"/api/v1/bills/{bill_b.id}/payments/{payment_a_id}", **auth_headers
        )
        assert get_resp.status_code == 404

        del_resp = api_client.delete(
            f"/api/v1/bills/{bill_b.id}/payments/{payment_a_id}", **auth_headers
        )
        assert del_resp.status_code == 404

        put_resp = api_client.put(
            f"/api/v1/bills/{bill_b.id}/payments/{payment_a_id}",
            data=json.dumps({"payment_status": "completed"}),
            content_type="application/json",
            **auth_headers,
        )
        assert put_resp.status_code == 404

    def test_paid_total_only_counts_own_bill(
        self, api_client, auth_headers, test_user_entity, bill_a, bill_b
    ):
        api_client.post(
            f"/api/v1/bills/{bill_a.id}/payments",
            data=json.dumps({"amount": 200, "payment_status": "completed", "payment_date": "2026-03-20"}),
            content_type="application/json",
            **auth_headers,
        )
        api_client.post(
            f"/api/v1/bills/{bill_a.id}/payments",
            data=json.dumps({"amount": 300, "payment_status": "completed", "payment_date": "2026-03-21"}),
            content_type="application/json",
            **auth_headers,
        )

        api_client.post(
            f"/api/v1/bills/{bill_b.id}/payments",
            data=json.dumps({"amount": 900, "payment_status": "completed", "payment_date": "2026-03-22"}),
            content_type="application/json",
            **auth_headers,
        )

        list_a = api_client.get(f"/api/v1/bills/{bill_a.id}/payments", **auth_headers)
        assert float(list_a.json()["paid_total"]) == 500

        list_b = api_client.get(f"/api/v1/bills/{bill_b.id}/payments", **auth_headers)
        assert float(list_b.json()["paid_total"]) == 900


@pytest.mark.django_db
class TestSameSupplierPaymentHistory:
    """Same xero_contact_id: payment history merges across bills."""

    @pytest.fixture
    def bill_supplier_a1(self, db, test_user, test_entity):
        return Bill.objects.create(
            entity_id=test_entity.id,
            contact="Acme Corp",
            xero_contact_id="contact-xero-001",
            reference="INV-A1",
            status="draft",
            amount=Decimal("20000.00"),
            uploaded_by=test_user.id,
        )

    @pytest.fixture
    def bill_supplier_a2(self, db, test_user, test_entity):
        return Bill.objects.create(
            entity_id=test_entity.id,
            contact="Acme Corp",
            xero_contact_id="contact-xero-001",
            reference="INV-B1",
            status="draft",
            amount=Decimal("30000.00"),
            uploaded_by=test_user.id,
        )

    def test_list_shows_payments_from_all_bills_same_xero_contact(
        self,
        api_client,
        auth_headers,
        test_user_entity,
        bill_supplier_a1,
        bill_supplier_a2,
    ):
        r1 = api_client.post(
            f"/api/v1/bills/{bill_supplier_a1.id}/payments",
            data=json.dumps({
                "amount": 20000,
                "payment_status": "completed",
                "payment_date": "2026-03-15",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert r1.status_code == 201
        pid1 = r1.json()["id"]

        r2 = api_client.post(
            f"/api/v1/bills/{bill_supplier_a2.id}/payments",
            data=json.dumps({
                "amount": 5000,
                "payment_status": "pending",
                "payment_date": "2026-03-30",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert r2.status_code == 201
        pid2 = r2.json()["id"]

        list_from_b2 = api_client.get(
            f"/api/v1/bills/{bill_supplier_a2.id}/payments", **auth_headers
        )
        assert list_from_b2.status_code == 200
        body = list_from_b2.json()
        assert len(body["payments"]) == 2
        ids = {p["id"] for p in body["payments"]}
        assert ids == {pid1, pid2}
        refs_by_id = {p["id"]: p["bill_reference"] for p in body["payments"]}
        assert refs_by_id[pid1] == "INV-A1"
        assert refs_by_id[pid2] == "INV-B1"
        # paid_total is for bill_supplier_a2 only
        assert float(body["paid_total"]) == 0

    def test_same_contact_without_xero_merges(
        self, api_client, auth_headers, test_user, test_entity, test_user_entity
    ):
        b1 = Bill.objects.create(
            entity_id=test_entity.id,
            contact="Local Vendor",
            xero_contact_id="",
            reference="L-1",
            status="draft",
            amount=Decimal("100.00"),
            uploaded_by=test_user.id,
        )
        b2 = Bill.objects.create(
            entity_id=test_entity.id,
            contact="Local Vendor",
            xero_contact_id="",
            reference="L-2",
            status="draft",
            amount=Decimal("200.00"),
            uploaded_by=test_user.id,
        )
        api_client.post(
            f"/api/v1/bills/{b1.id}/payments",
            data=json.dumps({"amount": 10, "payment_status": "pending", "payment_date": "2026-01-01"}),
            content_type="application/json",
            **auth_headers,
        )
        api_client.post(
            f"/api/v1/bills/{b2.id}/payments",
            data=json.dumps({"amount": 20, "payment_status": "pending", "payment_date": "2026-01-02"}),
            content_type="application/json",
            **auth_headers,
        )
        resp = api_client.get(f"/api/v1/bills/{b2.id}/payments", **auth_headers)
        assert len(resp.json()["payments"]) == 2
