"""
Tests for paid-bill immutability and automatic status calculation.

Rules:
  - When completed payments sum >= bill amount → bill status becomes "paid"
  - When completed payments sum > 0 but < bill amount → "partially_paid"
  - Paid bills are immutable for cashiers (no edits/deletes/payments); elevated
    roles can edit; increasing total above paid sum reverts to partially_paid
  - Payment history on paid bills remains viewable (GET still works)
"""

import json
from decimal import Decimal

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import Bill, Payment
from shared_models.models import Entity, User, UserEntity


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="ed7b2b76-4a02-5d0e-85f1-7e10dcd7bfeb",
        email="immut@minty.com",
        password="hashed",
        first_name="Immut",
        last_name="Tester",
        username="immuttester",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="9e48eede-f5e7-5c03-9eaa-d76b54c61e8e",
        name="Immut Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


@pytest.fixture
def membership(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


@pytest.fixture
def membership_cashier(db, user, entity):
    """Cashier cannot edit/delete paid bills (elevated roles can)."""
    return UserEntity.objects.create(user=user, entity=entity, role="cashier")


@pytest.fixture
def membership_accountant(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="accountant")


def _auth(user, entity):
    token = pyjwt.encode(
        {"user_id": user.id, "entity_id": entity.id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ENTITY_ID": entity.id,
    }


def _make_bill(entity, user, amount=100, status="submitted"):
    return Bill.objects.create(
        entity_id=entity.id,
        contact="Vendor",
        status=status,
        amount=amount,
        description="Immutability test",
        uploaded_by=user.id,
    )


def _make_payment(bill, user, amount, status="completed"):
    return Payment.objects.create(
        bill=bill,
        amount=amount,
        payment_status=status,
        created_by=user.id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# AUTO STATUS CALCULATION
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestAutoStatusCalculation:
    """Payment creation automatically updates bill status."""

    def test_single_partial_payment_sets_partially_paid(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=200)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "80.00",
                    "payment_status": "completed",
                }
            ),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 201
        bill.refresh_from_db()
        assert bill.status == "partially_paid"

    def test_full_payment_sets_paid(self, api, user, entity, membership):
        bill = _make_bill(entity, user, amount=100)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "100.00",
                    "payment_status": "completed",
                }
            ),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 201
        bill.refresh_from_db()
        assert bill.status == "paid"

    def test_multiple_partials_sum_to_paid(self, api, user, entity, membership):
        bill = _make_bill(entity, user, amount=300)
        headers = _auth(user, entity)

        for amt in ["100.00", "100.00", "100.00"]:
            resp = api.post(
                f"/api/v1/bills/{bill.id}/payments",
                data=json.dumps(
                    {
                        "amount": amt,
                        "payment_status": "completed",
                    }
                ),
                content_type="application/json",
                **headers,
            )
            assert resp.status_code == 201

        bill.refresh_from_db()
        assert bill.status == "paid"

    def test_overpayment_is_rejected(self, api, user, entity, membership):
        bill = _make_bill(entity, user, amount=50)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "75.00",
                    "payment_status": "completed",
                }
            ),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"].lower()
        bill.refresh_from_db()
        assert bill.status == "submitted"

    def test_pending_payment_does_not_change_status(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "100.00",
                    "payment_status": "pending",
                }
            ),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 201
        bill.refresh_from_db()
        assert bill.status == "submitted"

    def test_pending_payment_over_bill_total_rejected(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "100.01",
                    "payment_status": "pending",
                }
            ),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"].lower()

    def test_sum_of_pending_payments_cannot_exceed_bill_total(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "60.00",
                    "payment_status": "pending",
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "50.00",
                    "payment_status": "pending",
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"].lower()

    def test_updating_payment_to_completed_recalculates(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "100.00",
                    "payment_status": "pending",
                }
            ),
            content_type="application/json",
            **headers,
        )
        payment_id = resp.json()["id"]
        bill.refresh_from_db()
        assert bill.status == "submitted"

        resp = api.put(
            f"/api/v1/bills/{bill.id}/payments/{payment_id}",
            data=json.dumps({"payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 200
        bill.refresh_from_db()
        assert bill.status == "paid"

    def test_deleting_payment_recalculates_from_partially_paid(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=200)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps(
                {
                    "amount": "80.00",
                    "payment_status": "completed",
                }
            ),
            content_type="application/json",
            **headers,
        )
        payment_id = resp.json()["id"]
        bill.refresh_from_db()
        assert bill.status == "partially_paid"

        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{payment_id}",
            **headers,
        )
        assert resp.status_code == 200
        bill.refresh_from_db()
        assert bill.status == "partially_paid"

    def test_second_payment_exceeding_remainder_rejected(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "70.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "50.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"].lower()

    def test_exact_remaining_amount_accepted(self, api, user, entity, membership):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "60.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "40.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        bill.refresh_from_db()
        assert bill.status == "paid"

    def test_amount_due_decreases_while_amount_stays(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=1500)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "700.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        bill_resp = api.get(f"/api/v1/bills/{bill.id}", **headers).json()
        assert bill_resp["amount"] == "1500.00"
        assert bill_resp["amount_due"] == "800.00"
        assert bill_resp["status"] == "partially_paid"

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "300.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        bill_resp = api.get(f"/api/v1/bills/{bill.id}", **headers).json()
        assert bill_resp["amount"] == "1500.00"
        assert bill_resp["amount_due"] == "500.00"

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "500.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 201
        bill_resp = api.get(f"/api/v1/bills/{bill.id}", **headers).json()
        assert bill_resp["amount"] == "1500.00"
        assert bill_resp["amount_due"] == "0.00"
        assert bill_resp["status"] == "paid"

    def test_amount_due_restored_after_payment_deleted(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=500)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "200.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        payment_id = resp.json()["id"]
        bill_resp = api.get(f"/api/v1/bills/{bill.id}", **headers).json()
        assert bill_resp["amount_due"] == "300.00"

        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{payment_id}",
            **headers,
        )
        assert resp.status_code == 200
        bill_resp = api.get(f"/api/v1/bills/{bill.id}", **headers).json()
        assert bill_resp["amount"] == "500.00"
        assert bill_resp["amount_due"] == "500.00"

    def test_update_amount_exceeding_remainder_rejected(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "60.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        payment_id = resp.json()["id"]

        resp = api.put(
            f"/api/v1/bills/{bill.id}/payments/{payment_id}",
            data=json.dumps({"amount": "150.00"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 422
        assert "exceeds" in resp.json()["detail"].lower()

    def test_update_own_amount_within_limit_accepted(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, amount=100)
        headers = _auth(user, entity)

        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "60.00", "payment_status": "completed"}),
            content_type="application/json",
            **headers,
        )
        payment_id = resp.json()["id"]

        resp = api.put(
            f"/api/v1/bills/{bill.id}/payments/{payment_id}",
            data=json.dumps({"amount": "100.00"}),
            content_type="application/json",
            **headers,
        )
        assert resp.status_code == 200
        bill.refresh_from_db()
        assert bill.status == "paid"


# ═══════════════════════════════════════════════════════════════════════════
# ELEVATED EDIT: INCREASE TOTAL ON PAID BILL
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestElevatedEditIncreasesPaidBillTotal:
    """Accountant+ can raise a paid bill's total; status and payments API stay consistent."""

    def test_increase_amount_reopens_partially_paid_and_allows_new_payment(
        self, api, user, entity, membership_accountant
    ):
        bill = _make_bill(entity, user, amount=100, status="paid")
        _make_payment(bill, user, Decimal("100"), status="completed")

        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"amount": "150.00"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "partially_paid"
        assert Decimal(body["amount_due"]) == Decimal("50")

        bill.refresh_from_db()
        assert bill.status == "partially_paid"

        resp2 = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "50.00", "payment_status": "completed"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp2.status_code == 201
        bill.refresh_from_db()
        assert bill.status == "paid"


# ═══════════════════════════════════════════════════════════════════════════
# PAID BILL IMMUTABILITY
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestPaidBillImmutability:
    """Paid bills: cashiers cannot edit, void, add/update/delete payments, or add payments.

    Elevated roles (accountant/admin/super_admin) can edit, void, and delete payments
    per the billing RBAC matrix; create/update payment and new completed amounts on a
    fully paid bill remain blocked by ``check_bill_mutable``."""

    def _make_paid_bill(self, entity, user):
        bill = _make_bill(entity, user, amount=100, status="paid")
        _make_payment(bill, user, Decimal("100"), status="completed")
        return bill

    def test_cannot_edit_paid_bill(self, api, user, entity, membership_cashier):
        bill = self._make_paid_bill(entity, user)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Changed"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        assert "paid" in resp.json()["detail"].lower()

    def test_cannot_delete_paid_bill(self, api, user, entity, membership_cashier):
        bill = self._make_paid_bill(entity, user)
        resp = api.delete(
            f"/api/v1/bills/{bill.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        assert "paid" in resp.json()["detail"].lower()

    def test_cannot_create_payment_on_paid_bill(
        self, api, user, entity, membership_cashier
    ):
        bill = self._make_paid_bill(entity, user)
        resp = api.post(
            f"/api/v1/bills/{bill.id}/payments",
            data=json.dumps({"amount": "10.00", "payment_status": "completed"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        assert "immutable" in resp.json()["detail"].lower()

    def test_cannot_edit_payment_on_paid_bill(
        self, api, user, entity, membership_cashier
    ):
        bill = self._make_paid_bill(entity, user)
        payment = bill.payments.first()
        resp = api.put(
            f"/api/v1/bills/{bill.id}/payments/{payment.id}",
            data=json.dumps({"note": "changed"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        assert "immutable" in resp.json()["detail"].lower()

    def test_cannot_delete_payment_on_paid_bill(
        self, api, user, entity, membership_cashier
    ):
        bill = self._make_paid_bill(entity, user)
        payment = bill.payments.first()
        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{payment.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"].lower()
        assert "accountant" in detail or "paid" in detail

    def test_accountant_can_delete_payment_on_paid_bill(
        self, api, user, entity, membership_accountant
    ):
        bill = self._make_paid_bill(entity, user)
        payment = bill.payments.first()
        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{payment.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# PAID BILL STILL VIEWABLE
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestPaidBillViewable:
    """Payment history on paid bills remains viewable."""

    def _make_paid_bill(self, entity, user):
        bill = _make_bill(entity, user, amount=100, status="paid")
        _make_payment(bill, user, Decimal("100"), status="completed")
        return bill

    def test_can_view_paid_bill(self, api, user, entity, membership):
        bill = self._make_paid_bill(entity, user)
        resp = api.get(
            f"/api/v1/bills/{bill.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "paid"

    def test_can_list_payments_on_paid_bill(self, api, user, entity, membership):
        bill = self._make_paid_bill(entity, user)
        resp = api.get(
            f"/api/v1/bills/{bill.id}/payments",
            **_auth(user, entity),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["payments"]) == 1
        assert Decimal(body["paid_total"]) == Decimal("100")

    def test_can_view_single_payment_on_paid_bill(self, api, user, entity, membership):
        bill = self._make_paid_bill(entity, user)
        payment = bill.payments.first()
        resp = api.get(
            f"/api/v1/bills/{bill.id}/payments/{payment.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 200
        assert resp.json()["amount"] == "100.00"
