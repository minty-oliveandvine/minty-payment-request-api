"""
Tests to verify DEPRECIATN account type is excluded from the bill settings list.

The bill settings accounts list is restricted to exactly 8 permitted Xero account
types (CURRENT, NONCURRENT, CURRLIAB, TERMLIAB, FIXED, INVENTORY, DIRECTCOSTS,
EXPENSE).  DEPRECIATN is NOT in that set and must not appear in either the default
list or the bill_dropdown list.  It can still be retrieved via an explicit
account_type= query parameter.
"""

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
        id="deprec-user",
        email="deprec@minty.com",
        password="hashed",
        first_name="Deprec",
        last_name="Test",
        username="deprectest",
        system_role="user",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="deprec-entity",
        name="Deprec Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="active",
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
def test_depreciatn_account_excluded_from_default_list(
    api, entity, auth_headers, user_entity
):
    """
    DEPRECIATN is not in BILL_SETTINGS_ACCOUNT_TYPES and must be excluded from
    the default account list.
    """
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-1",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Expense A",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-1",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}
    types = {row["account_type"] for row in data}

    assert "760" not in codes, "DEPRECIATN account must not appear in default list"
    assert "DEPRECIATN" not in types
    assert "600" in codes, "EXPENSE account must still appear"
    assert len(data) == 1


@pytest.mark.django_db
def test_depreciatn_account_excluded_with_bill_dropdown_filter(
    api, entity, auth_headers, user_entity
):
    """
    DEPRECIATN is excluded when bill_dropdown=true because it is not in the
    permitted account type set.
    """
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
        account_code="820",
        account_name="Bank Account",
        account_type="BANK",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-bank-1",
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

    url = "/api/v1/entity-bill-accounts/?sync_chart=false&bill_dropdown=true"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}

    assert "760" not in codes, "DEPRECIATN must not appear with bill_dropdown filter"
    assert "820" not in codes, "BANK must not appear"
    assert "610" in codes, "DIRECTCOSTS must appear"


@pytest.mark.django_db
def test_depreciatn_account_reachable_via_explicit_account_type_filter(
    api, entity, auth_headers, user_entity
):
    """
    DEPRECIATN can still be retrieved by an explicit account_type= query, which
    bypasses the allowlist.
    """
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation A",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-3",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="761",
        account_name="Depreciation B",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-4",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Expense",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-2",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false&account_type=DEPRECIATN"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)

    assert len(data) == 2
    codes = {row["account_code"] for row in data}
    assert codes == {"760", "761"}
    for row in data:
        assert row["account_type"] == "DEPRECIATN"


@pytest.mark.django_db
def test_depreciatn_mixed_with_permitted_types_only_permitted_returned(
    api, entity, auth_headers, user_entity
):
    """
    When DEPRECIATN rows coexist with permitted types, only permitted types appear
    in the default list.
    """
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-5",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Office Expense",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-3",
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
        account_name="Current Liability",
        account_type="CURRLIAB",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-liab-1",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}
    types = {row["account_type"] for row in data}

    assert codes == {"600", "610", "820"}
    assert types == {"EXPENSE", "DIRECTCOSTS", "CURRLIAB"}
    assert "DEPRECIATN" not in types


@pytest.mark.django_db
def test_inactive_depreciatn_excluded_regardless(
    api, entity, auth_headers, user_entity
):
    """
    Inactive DEPRECIATN accounts are excluded for two reasons: type not permitted
    and is_active=false.
    """
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Active Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-6",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="761",
        account_name="Inactive Depreciation",
        account_type="DEPRECIATN",
        is_active=False,
        is_deleted=False,
        xero_account_id="xero-deprec-7",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Active Expense",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-4",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}

    assert (
        "760" not in codes
    ), "Active DEPRECIATN must still be excluded (type not permitted)"
    assert "761" not in codes
    assert "600" in codes


@pytest.mark.django_db
def test_deleted_depreciatn_account_excluded_by_default(
    api, entity, auth_headers, user_entity
):
    """
    Deleted DEPRECIATN accounts are excluded regardless of type filter or
    include_deleted flag default.
    """
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Active Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-8",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="761",
        account_name="Deleted Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=True,
        xero_account_id="xero-deprec-9",
    )
    EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="600",
        account_name="Active Expense",
        account_type="EXPENSE",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-exp-5",
    )

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    codes = {row["account_code"] for row in data}

    assert "760" not in codes, "DEPRECIATN excluded even when not deleted"
    assert "761" not in codes
    assert "600" in codes

    # Even with include_deleted=true, DEPRECIATN type is still filtered out
    url_with_deleted = (
        "/api/v1/entity-bill-accounts/?sync_chart=false&include_deleted=true"
    )
    resp2 = api.get(url_with_deleted, **auth_headers)
    assert resp2.status_code == 200
    data2 = json.loads(resp2.content)
    codes2 = {row["account_code"] for row in data2}
    assert "760" not in codes2
    assert "761" not in codes2
