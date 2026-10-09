"""
Void-transition tests: draft hard-delete + non-draft soft void + immutability.

Draft: DELETE returns 200, bill row removed, message "Bill deleted".
Other statuses: DELETE voids (status → "voided"), row kept, VOIDED audit.

After void (non-draft origin), PUT /bills/{id} and PUT /bills/{id}/draft return 403.

Permission note
───────────────
  check_delete_bill() allows ALL_BILL_ROLES for non-paid statuses.
  For paid status it requires an elevated role (accountant/admin/super_admin).
  All tests in this file use the 'admin' role, which satisfies both tiers.
"""

import json
from decimal import Decimal

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import Audit, Bill, Payment
from shared_models.models import Entity, User, UserEntity

# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="144d4449-4bc4-5f53-9a6d-bd9701c7cfc7",
        email="void@minty.com",
        password="hashed",
        first_name="Void",
        last_name="Tester",
        username="voidtester",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="f5f20323-4780-5669-b7bb-2a5de997d984",
        name="Void Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


@pytest.fixture
def membership(db, user, entity):
    # 'admin' satisfies both ALL_BILL_ROLES and ELEVATED_ROLES
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


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


def _make_bill(entity, user, status, amount=500):
    return Bill.objects.create(
        entity_id=entity.id,
        contact="Test Vendor",
        status=status,
        amount=amount,
        description="Void transition test",
        uploaded_by=user.id,
    )


def _add_completed_payment(bill, user, amount):
    """Directly insert a completed payment (bypasses API overpayment guard)."""
    return Payment.objects.create(
        bill=bill,
        amount=Decimal(str(amount)),
        payment_status="completed",
        created_by=user.id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS — reusable assertion sequences
# ═══════════════════════════════════════════════════════════════════════════


def _assert_void_succeeds(api, bill, auth_headers):
    """DELETE returns 200, body says 'voided', DB row still exists with status=voided."""
    resp = api.delete(f"/api/v1/bills/{bill.id}", **auth_headers)
    assert (
        resp.status_code == 200
    ), f"Expected 200 voiding a {bill.status!r} bill, got {resp.status_code}: {resp.json()}"
    assert (
        "void" in resp.json().get("message", "").lower()
    ), f"Expected 'void' in response message, got: {resp.json()}"

    # Soft-delete: row must still exist
    bill.refresh_from_db()
    assert (
        bill.status == "void"
    ), f"Expected DB status='void' after DELETE, got '{bill.status}'"


def _assert_draft_hard_delete_succeeds(api, bill, auth_headers):
    """DELETE returns 200, message 'deleted', bill row removed."""
    resp = api.delete(f"/api/v1/bills/{bill.id}", **auth_headers)
    assert (
        resp.status_code == 200
    ), f"Expected 200 deleting draft bill, got {resp.status_code}: {resp.json()}"
    assert (
        "delet" in resp.json().get("message", "").lower()
    ), f"Expected delete wording in response message, got: {resp.json()}"
    assert not Bill.objects.filter(pk=bill.id).exists()


def _assert_update_blocked(api, bill_id, auth_headers):
    """PUT /bills/{id} on a voided bill must return 403 with voided-related message."""
    resp = api.put(
        f"/api/v1/bills/{bill_id}",
        data=json.dumps({"contact": "Attempt after void"}),
        content_type="application/json",
        **auth_headers,
    )
    assert (
        resp.status_code == 403
    ), f"Expected 403 editing voided bill via PUT /bills/{{id}}, got {resp.status_code}: {resp.json()}"
    body_text = json.dumps(resp.json()).lower()
    assert "void" in body_text, f"Expected 'void' in 403 body, got: {resp.json()}"


def _assert_draft_update_blocked(api, bill_id, auth_headers):
    """PUT /bills/{id}/draft on a voided bill must return 403 with voided-related message."""
    resp = api.put(
        f"/api/v1/bills/{bill_id}/draft",
        data=json.dumps({"contact": "Draft attempt after void"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 403, (
        f"Expected 403 editing voided bill via PUT /bills/{{id}}/draft, "
        f"got {resp.status_code}: {resp.json()}"
    )
    body_text = json.dumps(resp.json()).lower()
    assert "void" in body_text, f"Expected 'void' in 403 body, got: {resp.json()}"


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS — void soft-delete semantics
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestVoidIsSoftDelete:
    """Non-draft DELETE sets status=voided; the bill row is kept."""

    @pytest.mark.parametrize(
        "initial_status",
        [
            "submitted",
            "returned",
            "partially_paid",
            "paid",
        ],
    )
    def test_bill_row_survives_void(
        self, api, user, entity, membership, initial_status
    ):
        bill = _make_bill(entity, user, status=initial_status)
        if initial_status == "partially_paid":
            _add_completed_payment(bill, user, 100)
        bill_id = bill.id

        resp = api.delete(f"/api/v1/bills/{bill_id}", **_auth(user, entity))
        assert resp.status_code == 200

        # Row must still be fetchable
        surviving = Bill.objects.get(pk=bill_id)
        assert surviving.status == "void"

    @pytest.mark.parametrize(
        "initial_status",
        [
            "submitted",
            "returned",
            "partially_paid",
            "paid",
        ],
    )
    def test_void_response_message(self, api, user, entity, membership, initial_status):
        bill = _make_bill(entity, user, status=initial_status)
        if initial_status == "partially_paid":
            _add_completed_payment(bill, user, 100)

        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 200
        assert "void" in resp.json().get("message", "").lower()

    def test_draft_delete_response_message(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="draft")
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 200
        assert "delet" in resp.json().get("message", "").lower()

    @pytest.mark.parametrize(
        "initial_status",
        [
            "submitted",
            "returned",
            "partially_paid",
            "paid",
        ],
    )
    def test_void_writes_audit_entry(
        self, api, user, entity, membership, initial_status
    ):
        bill = _make_bill(entity, user, status=initial_status)
        if initial_status == "partially_paid":
            _add_completed_payment(bill, user, 100)

        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))

        assert Audit.objects.filter(
            bill=bill, action=Audit.Action.VOIDED
        ).exists(), (
            f"Expected a VOIDED audit entry for initial_status={initial_status!r}"
        )

    def test_voided_bill_is_readable_via_get(self, api, user, entity, membership):
        """GET on a voided bill must still return 200 — it is not erased."""
        bill = _make_bill(entity, user, status="submitted")
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))

        resp = api.get(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 200
        assert resp.json()["status"] == "void"


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS — void from each initial status
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestVoidFromDraft:
    """A DRAFT bill is hard-deleted (no voided row)."""

    def test_delete_draft_returns_200(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="draft")
        _assert_draft_hard_delete_succeeds(api, bill, _auth(user, entity))

    def test_deleted_draft_not_found_on_get(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="draft")
        bid = bill.id
        api.delete(f"/api/v1/bills/{bid}", **_auth(user, entity))
        resp = api.get(f"/api/v1/bills/{bid}", **_auth(user, entity))
        assert resp.status_code == 404

    def test_deleted_draft_not_found_on_put(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="draft")
        bid = bill.id
        api.delete(f"/api/v1/bills/{bid}", **_auth(user, entity))
        resp = api.put(
            f"/api/v1/bills/{bid}",
            data=json.dumps({"contact": "Attempt after delete"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 404

    def test_deleted_draft_not_found_on_put_draft(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="draft")
        bid = bill.id
        api.delete(f"/api/v1/bills/{bid}", **_auth(user, entity))
        resp = api.put(
            f"/api/v1/bills/{bid}/draft",
            data=json.dumps({"contact": "Draft attempt after delete"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestVoidFromPartiallyPaid:
    """A PARTIALLY_PAID bill (has completed payments) can be voided and becomes fully immutable."""

    def _make_partially_paid(self, entity, user):
        bill = _make_bill(entity, user, status="partially_paid", amount=500)
        _add_completed_payment(bill, user, 200)
        return bill

    def test_void_partially_paid_returns_200(self, api, user, entity, membership):
        bill = self._make_partially_paid(entity, user)
        _assert_void_succeeds(api, bill, _auth(user, entity))

    def test_void_partially_paid_blocks_full_update(
        self, api, user, entity, membership
    ):
        bill = self._make_partially_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_partially_paid_blocks_draft_update(
        self, api, user, entity, membership
    ):
        bill = self._make_partially_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_draft_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_partially_paid_status_in_db(self, api, user, entity, membership):
        bill = self._make_partially_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        bill.refresh_from_db()
        assert bill.status == "void"

    def test_payments_survive_void(self, api, user, entity, membership):
        """Voiding a partially-paid bill must NOT delete its payment history."""
        bill = self._make_partially_paid(entity, user)
        payment_count_before = bill.payments.count()

        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))

        bill.refresh_from_db()
        assert (
            bill.payments.count() == payment_count_before
        ), "Payment records must survive a void operation"


@pytest.mark.django_db
class TestVoidFromPaid:
    """A PAID bill requires an elevated role to void; afterwards it is fully immutable."""

    def _make_paid(self, entity, user):
        bill = _make_bill(entity, user, status="paid", amount=300)
        _add_completed_payment(bill, user, 300)
        return bill

    def test_void_paid_returns_200_for_admin(self, api, user, entity, membership):
        """Admin role satisfies the elevated requirement for voiding a paid bill."""
        bill = self._make_paid(entity, user)
        _assert_void_succeeds(api, bill, _auth(user, entity))

    def test_void_paid_blocks_full_update(self, api, user, entity, membership):
        bill = self._make_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_paid_blocks_draft_update(self, api, user, entity, membership):
        bill = self._make_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_draft_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_paid_status_in_db(self, api, user, entity, membership):
        bill = self._make_paid(entity, user)
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        bill.refresh_from_db()
        assert bill.status == "void"

    def test_cashier_cannot_void_paid_bill(self, api, user, entity, db):
        """Non-elevated roles must receive 403 when attempting to void a paid bill."""
        UserEntity.objects.filter(user=user, entity=entity).delete()
        UserEntity.objects.create(user=user, entity=entity, role="cashier")

        bill = self._make_paid(entity, user)
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 403

        # Bill must remain paid — not voided
        bill.refresh_from_db()
        assert bill.status == "paid"


@pytest.mark.django_db
class TestVoidFromSubmitted:
    """A SUBMITTED bill can be voided and then becomes fully immutable."""

    def test_void_submitted_returns_200(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="submitted")
        _assert_void_succeeds(api, bill, _auth(user, entity))

    def test_void_submitted_blocks_full_update(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="submitted")
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_submitted_blocks_draft_update(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="submitted")
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        _assert_draft_update_blocked(api, bill.id, _auth(user, entity))

    def test_void_submitted_status_in_db(self, api, user, entity, membership):
        bill = _make_bill(entity, user, status="submitted")
        api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        bill.refresh_from_db()
        assert bill.status == "void"


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS — idempotency / double-void
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestDoubleVoid:
    """
    Second DELETE on a draft that was hard-deleted returns 404.
    Second DELETE on an already-voided bill still succeeds (200) — current behaviour.
    """

    def test_second_delete_on_removed_draft_returns_404(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, status="draft")
        auth = _auth(user, entity)
        bid = bill.id

        first = api.delete(f"/api/v1/bills/{bid}", **auth)
        assert first.status_code == 200

        second = api.delete(f"/api/v1/bills/{bid}", **auth)
        assert second.status_code == 404

    def test_double_void_voided_bill_current_behaviour(
        self, api, user, entity, membership
    ):
        bill = _make_bill(entity, user, status="submitted")
        auth = _auth(user, entity)

        first = api.delete(f"/api/v1/bills/{bill.id}", **auth)
        assert first.status_code == 200

        second = api.delete(f"/api/v1/bills/{bill.id}", **auth)
        assert second.status_code == 200

        bill.refresh_from_db()
        assert bill.status == "void"


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS — voided bills cannot receive new payments
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestVoidedBillPaymentBlocked:
    """No new payment operations are allowed once a bill is voided."""

    @pytest.mark.parametrize(
        "initial_status",
        [
            "draft",
            "submitted",
            "returned",
            "partially_paid",
        ],
    )
    def test_cannot_add_payment_to_voided_bill(
        self, api, user, entity, membership, initial_status
    ):
        bill = _make_bill(entity, user, status=initial_status)
        if initial_status == "partially_paid":
            _add_completed_payment(bill, user, 100)
        auth = _auth(user, entity)
        bid = bill.id

        api.delete(f"/api/v1/bills/{bid}", **auth)

        resp = api.post(
            f"/api/v1/bills/{bid}/payments",
            data=json.dumps({"amount": "50.00", "payment_status": "completed"}),
            content_type="application/json",
            **auth,
        )
        if initial_status == "draft":
            assert resp.status_code == 404, (
                f"Expected 404 adding payment to hard-deleted draft bill, "
                f"got {resp.status_code}: {resp.json()}"
            )
        else:
            assert resp.status_code == 403, (
                f"Expected 403 adding payment to voided bill "
                f"(initial_status={initial_status!r}), got {resp.status_code}: {resp.json()}"
            )
