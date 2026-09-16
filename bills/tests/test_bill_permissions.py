"""
Role-based permission tests for Bill operations.

Tests the full permission matrix:
    ┌───────────────────────────────┬───────────────────────────────────────────┐
    │ Action                        │ Allowed Roles                             │
    ├───────────────────────────────┼───────────────────────────────────────────┤
    │ Create Bill                   │ cashier, shop_manager, accountant,        │
    │                               │ admin, super_admin                        │
    │ Edit Bill (Draft / Submitted) │ cashier, shop_manager, accountant,        │
    │                               │ admin, super_admin                        │
    │ Edit Bill (Paid / Partially)  │ accountant, admin, super_admin            │
    │ Change Paid Status            │ accountant, admin, super_admin            │
    │ Delete Bill (Draft/Submitted) │ cashier, shop_manager, accountant,        │
    │                               │ admin, super_admin                        │
    │ Delete Bill (Paid / Partially)│ accountant, admin, super_admin            │
    │ Publish / Republish to Xero   │ accountant, admin, super_admin            │
    │ Delete Payment                │ accountant, admin, super_admin (any bill  │
    │                               │ status except role gate only)             │
    └───────────────────────────────┴───────────────────────────────────────────┘
"""

import json
from unittest.mock import patch

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import Bill, Payment
from shared_models.models import Entity, User, UserEntity

ALL_ROLES = ["cashier", "shop_manager", "accountant", "admin", "super_admin"]
ELEVATED_ROLES = ["accountant", "admin", "super_admin"]
BASIC_ROLES = ["cashier", "shop_manager"]
DENIED_ROLE = "viewer"


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="c428cdf9-479b-509f-9850-92c75f8add26",
        email="perm@minty.com",
        password="hashed",
        first_name="Perm",
        last_name="Tester",
        username="permtester",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="a13dcc34-7315-5ca9-9bc7-c014a0f5bfe2",
        name="Perm Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


def _set_role(user, entity, role):
    UserEntity.objects.filter(user=user, entity=entity).delete()
    return UserEntity.objects.create(user=user, entity=entity, role=role)


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


def _make_bill(entity, user, status="draft"):
    return Bill.objects.create(
        entity_id=entity.id,
        contact="Test Vendor",
        status=status,
        amount=100,
        description="Permission test bill",
        uploaded_by=user.id,
    )


def _make_completed_payment(bill, user, amount="100.00"):
    return Payment.objects.create(
        bill=bill,
        payment_date="2026-03-01",
        amount=amount,
        currency_code="HKD",
        payment_status=Payment.PaymentStatus.COMPLETED,
        created_by=user.id,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 0. ROLE STRING NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestRoleNormalization:
    """Spaces and hyphens in DB role strings map to the same matrix keys."""

    def test_shop_manager_with_space_can_create_bill(self, api, user, entity):
        _set_role(user, entity, "shop manager")
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 201

    def test_super_admin_hyphen_normalized_for_publish_gate(self):
        from core.permissions import check_publish_xero

        check_publish_xero("super-admin")  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# 1. CREATE BILL
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestCreateBillPermissions:
    """Create Bill: cashier, shop_manager, accountant, admin, super_admin → 201
    Unauthorized role → 403"""

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_allowed_roles_can_create(self, api, user, entity, role):
        _set_role(user, entity, role)
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert (
            resp.status_code == 201
        ), f"Role '{role}' should be allowed to create bills"

    def test_denied_role_cannot_create(self, api, user, entity):
        _set_role(user, entity, DENIED_ROLE)
        resp = api.post(
            "/api/v1/bills/",
            data=json.dumps({"contact": "Vendor"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 2. EDIT BILL (Draft / Submitted)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestEditDraftSubmittedBillPermissions:
    """Edit Bill (draft/submitted): all bill roles → 200
    Unauthorized role → 403"""

    @pytest.mark.parametrize("status", ["draft", "submitted"])
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_allowed_roles_can_edit(self, api, user, entity, role, status):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Updated"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 200, f"Role '{role}' should edit {status} bills"

    @pytest.mark.parametrize("status", ["draft", "submitted"])
    def test_denied_role_cannot_edit(self, api, user, entity, status):
        _set_role(user, entity, DENIED_ROLE)
        bill = _make_bill(entity, user, status=status)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Updated"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 3. EDIT BILL (Paid / Partially paid)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestEditPaidBillPermissions:
    """Edit Bill (paid / partially_paid): elevated → 200; cashier / shop_manager → 403."""

    @pytest.mark.parametrize("status", ["paid", "partially_paid"])
    @pytest.mark.parametrize("role", ELEVATED_ROLES)
    def test_elevated_roles_can_edit_paid_like_bill(
        self,
        api,
        user,
        entity,
        role,
        status,
    ):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Updated"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 200, f"Role '{role}' should edit {status} bills"

    @pytest.mark.parametrize("status", ["paid", "partially_paid"])
    @pytest.mark.parametrize("role", BASIC_ROLES)
    def test_basic_roles_cannot_edit_paid_like_bill(
        self,
        api,
        user,
        entity,
        role,
        status,
    ):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"contact": "Updated"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403, f"Role '{role}' should NOT edit {status} bills"


# ═══════════════════════════════════════════════════════════════════════════
# 4. CHANGE PAID STATUS
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestChangePaidStatusPermissions:
    """Change status to paid: accountant, admin, super_admin → 200
    cashier, shop_manager → 403"""

    @pytest.mark.parametrize("role", ELEVATED_ROLES)
    def test_elevated_roles_can_mark_paid(self, api, user, entity, role):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status="draft")
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"status": "paid"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 200, f"Role '{role}' should mark bills as paid"
        assert resp.json()["status"] == "paid"

    @pytest.mark.parametrize("role", BASIC_ROLES)
    def test_basic_roles_cannot_mark_paid(self, api, user, entity, role):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status="draft")
        resp = api.put(
            f"/api/v1/bills/{bill.id}",
            data=json.dumps({"status": "paid"}),
            content_type="application/json",
            **_auth(user, entity),
        )
        assert resp.status_code == 403, f"Role '{role}' should NOT mark bills as paid"


# ═══════════════════════════════════════════════════════════════════════════
# 5. DELETE BILL (Draft / Submitted)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestDeleteDraftSubmittedBillPermissions:
    """Delete Bill (draft/submitted): all bill roles → 200
    Unauthorized role → 403"""

    @pytest.mark.parametrize("status", ["draft", "submitted"])
    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_allowed_roles_can_delete(self, api, user, entity, role, status):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 200, f"Role '{role}' should delete {status} bills"

    @pytest.mark.parametrize("status", ["draft", "submitted"])
    def test_denied_role_cannot_delete(self, api, user, entity, status):
        _set_role(user, entity, DENIED_ROLE)
        bill = _make_bill(entity, user, status=status)
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 6. DELETE BILL (Paid / Partially paid)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestDeletePaidBillPermissions:
    """Delete Bill (paid / partially_paid): elevated → voids bill; basic roles → 403."""

    @pytest.mark.parametrize("status", ["paid", "partially_paid"])
    @pytest.mark.parametrize("role", ELEVATED_ROLES)
    def test_elevated_roles_can_void_paid_like_bill(
        self,
        api,
        user,
        entity,
        role,
        status,
    ):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 200, f"Role '{role}' should void {status} bills"
        bill.refresh_from_db()
        assert bill.status == "voided"

    @pytest.mark.parametrize("status", ["paid", "partially_paid"])
    @pytest.mark.parametrize("role", BASIC_ROLES)
    def test_basic_roles_cannot_delete_paid_like_bill(
        self,
        api,
        user,
        entity,
        role,
        status,
    ):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=status)
        resp = api.delete(f"/api/v1/bills/{bill.id}", **_auth(user, entity))
        assert resp.status_code == 403, f"Role '{role}' should NOT void {status} bills"


# ═══════════════════════════════════════════════════════════════════════════
# 7. PUBLISH / REPUBLISH TO XERO (permission check only)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestPublishXeroPermissions:
    """Publish to Xero: accountant, admin, super_admin → allowed
    cashier, shop_manager → denied (helper + HTTP)."""

    @pytest.mark.parametrize("role", ELEVATED_ROLES)
    def test_elevated_roles_allowed(self, role):
        from core.permissions import check_publish_xero

        check_publish_xero(role)  # should not raise

    @pytest.mark.parametrize("role", BASIC_ROLES)
    def test_basic_roles_denied(self, role):
        from core.exceptions import PermissionDeniedError
        from core.permissions import check_publish_xero

        with pytest.raises(PermissionDeniedError):
            check_publish_xero(role)

    @patch("bills.api.publish_bill_to_xero")
    @patch("bills.api.resolve_xero_access_token_for_entity")
    def test_http_publish_denied_for_cashier(
        self,
        mock_token,
        mock_publish,
        api,
        user,
        entity,
    ):
        _set_role(user, entity, "cashier")
        bill = _make_bill(entity, user, status="submitted")
        resp = api.post(
            f"/api/v1/bills/{bill.id}/publish/",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
        mock_publish.assert_not_called()
        mock_token.assert_not_called()

    @patch("bills.api.publish_bill_to_xero")
    @patch("bills.api.resolve_xero_access_token_for_entity")
    def test_http_publish_allowed_for_accountant(
        self,
        mock_token,
        mock_publish,
        api,
        user,
        entity,
    ):
        _set_role(user, entity, "accountant")
        bill = _make_bill(entity, user, status="submitted")
        mock_token.return_value = "xero-token"
        mock_publish.return_value = {"bill": bill}
        resp = api.post(
            f"/api/v1/bills/{bill.id}/publish/",
            **_auth(user, entity),
        )
        assert resp.status_code == 200
        mock_publish.assert_called_once()
        mock_token.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# 8. DELETE PAYMENT (no bill-status gate; elevated only)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.django_db
class TestDeletePaymentPermissions:
    """Delete payment: elevated roles may delete even when bill is paid."""

    @pytest.mark.parametrize(
        "bill_status",
        ["draft", "submitted", "paid", "partially_paid", "authorised"],
    )
    @pytest.mark.parametrize("role", ELEVATED_ROLES)
    def test_elevated_can_delete_payment(
        self,
        api,
        user,
        entity,
        role,
        bill_status,
    ):
        _set_role(user, entity, role)
        bill = _make_bill(entity, user, status=bill_status)
        pay = _make_completed_payment(bill, user)
        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{pay.id}",
            **_auth(user, entity),
        )
        assert (
            resp.status_code == 200
        ), f"Role {role} should delete payment on {bill_status} bill"

    def test_cashier_cannot_delete_payment_even_on_draft(
        self,
        api,
        user,
        entity,
    ):
        _set_role(user, entity, "cashier")
        bill = _make_bill(entity, user, status="draft")
        pay = _make_completed_payment(bill, user)
        resp = api.delete(
            f"/api/v1/bills/{bill.id}/payments/{pay.id}",
            **_auth(user, entity),
        )
        assert resp.status_code == 403
