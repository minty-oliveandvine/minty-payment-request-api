"""Tests for GET /entity-bill-accounts/ list filters (bill_dropdown, account_type)."""

import json

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import EntityBillAccountXero
from shared_models.models import Entity, User, UserEntity


@pytest.fixture
def api():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        id="cca61efb-248a-55cd-976d-e42732f398a0",
        email="acct@minty.com",
        password="hashed",
        first_name="Acct",
        last_name="List",
        username="acctlist",
        system_role="normal",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="a66d1e23-a0a3-585b-a656-f6437e173550",
        name="Acct Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
    )


@pytest.fixture
def user_entity(db, user, entity):
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


@pytest.fixture
def auth_headers(user, entity):
    token = pyjwt.encode(
        {"user_id": user.id, "entity_id": entity.id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_X_ENTITY_ID": entity.id,
    }


@pytest.mark.django_db
def test_default_list_includes_only_allowed_types(
    api, entity, auth_headers, user_entity
):
    """Default list (no filters) includes only the 8 permitted account types."""
    # Permitted types
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Expense A",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-1",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="610",
        account_name="Direct Cost",
        account_type="DIRECTCOSTS",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-dc-1",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="820",
        account_name="Overheads",
        account_type="OVERHEADS",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-liab-1",
    )
    # Non-permitted type — should be excluded
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="200",
        account_name="Bank Account",
        account_type="BANK",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-bank-1",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-1",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}
    types = {row["account_type"] for row in data}
    # Only the 3 permitted-type accounts should appear
    assert codes == {"600", "610", "820"}
    assert "BANK" not in types
    assert "DEPRECIATN" not in types


@pytest.mark.django_db
def test_bill_dropdown_includes_only_allowed_types(
    api, entity, auth_headers, user_entity
):
    """bill_dropdown=true also restricts to the 8 permitted account types."""
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Expense A",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-2",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="610",
        account_name="Direct Cost",
        account_type="DIRECTCOSTS",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-dc-2",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="820",
        account_name="Overheads",
        account_type="OVERHEADS",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-liab-2",
    )
    # Should be excluded regardless of bill_dropdown flag
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-2",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="900",
        account_name="Equity Account",
        account_type="EQUITY",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-equity-1",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false&bill_dropdown=true"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}
    assert codes == {"600", "610", "820"}


@pytest.mark.django_db
def test_all_permitted_types_appear(api, entity, auth_headers, user_entity):
    """Every permitted account type (BILL_SETTINGS_ACCOUNT_TYPES - five, decided 2026-09-17)
    is returned; nothing else is."""
    permitted = [
        ("300", "Fixed Asset", "FIXED", "xero-fa-1"),
        ("500", "Overheads", "OVERHEADS", "xero-oh-1"),
        ("550", "Prepayment", "PREPAYMENT", "xero-pp-1"),
        ("600", "Direct Cost", "DIRECTCOSTS", "xero-dc-3"),
        ("700", "Expense", "EXPENSE", "xero-exp-3"),
    ]
    for code, name, acc_type, xero_id in permitted:
        EntityBillAccountXero.objects.create(
            entity_id=entity.id,
            account_code=code,
            account_name=name,
            account_type=acc_type,
            is_active=True,
            is_deleted=False,
            xero_account_id=xero_id,
        )
    # Non-permitted rows
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="800",
        account_name="Bank",
        account_type="BANK",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-bank-2",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="810",
        account_name="Equity",
        account_type="EQUITY",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-equity-2",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="820",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-3",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    returned_types = {row["account_type"] for row in data}
    assert returned_types == {
        "DIRECTCOSTS",
        "EXPENSE",
        "FIXED",
        "OVERHEADS",
        "PREPAYMENT",
    }
    assert len(data) == 5


@pytest.mark.django_db
def test_explicit_account_type_overrides_default_filter(
    api, entity, auth_headers, user_entity
):
    """An explicit account_type= query bypasses the allowlist and returns exactly
    those types, even if they are not in BILL_SETTINGS_ACCOUNT_TYPES."""
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-4",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="820",
        account_name="Overheads",
        account_type="OVERHEADS",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-liab-3",
    )
    url = "/api/v1/entity-bill-accounts/?sync_chart=false&account_type=DEPRECIATN"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["account_type"] == "DEPRECIATN"


@pytest.mark.django_db
def test_explicit_account_type_with_bill_dropdown_uses_explicit_type(
    api, entity, auth_headers, user_entity
):
    """When account_type= is given, bill_dropdown= is ignored; the explicit type
    wins even if it is outside the allowlist."""
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="820",
        account_name="Current Liability",
        account_type="CURRLIAB",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-liab-4",
    )
    url = (
        "/api/v1/entity-bill-accounts/?sync_chart=false"
        "&bill_dropdown=true&account_type=CURRLIAB"
    )
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["account_type"] == "CURRLIAB"
