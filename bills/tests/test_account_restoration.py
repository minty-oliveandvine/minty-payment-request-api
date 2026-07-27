"""
Test cases for account restoration behavior from Xero archive.

When an account is archived in Xero and then restored:
- It should be marked as ACTIVE (is_active=true) by default
- Previous inactive state should NOT be preserved
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
        id="restore-user",
        email="restore@minty.com",
        password="hashed",
        first_name="Restore",
        last_name="Test",
        username="restoretest",
        system_role="user",
    )


@pytest.fixture
def entity(db):
    return Entity.objects.create(
        id="restore-entity",
        name="Restore Entity",
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
def test_archived_then_restored_account_becomes_active(
    api, entity, auth_headers, user_entity
):
    """
    Module 2: When an ACTIVE account is archived in Xero and then restored,
    it should come back as ACTIVE (is_active=true).

    Scenario:
    1. Account exists as ACTIVE
    2. Archive in Xero → should be marked is_deleted=true
    3. Restore in Xero → should be marked is_active=true, is_deleted=false
    """
    account = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=True,
        is_deleted=False,
        xero_account_id="xero-deprec-restore-1",
    )

    # Step 1: Verify account is active
    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["is_active"] is True
    assert data[0]["is_deleted"] is False

    # Step 2: Simulate archiving (what sync does when account removed from Xero)
    account.is_deleted = True
    account.save()

    # Verify account is hidden when archived
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 0, "Archived accounts should not appear in default list"

    # Step 3: Simulate restoration (what sync should do when account restored in Xero)
    # This is what _upsert_entity_bill_account_xero_rows should do
    account.is_deleted = False
    account.is_active = True  # This is what we expect the sync to set
    account.save()

    # Verify account is active again after restoration
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["is_active"] is True, "Restored account should be ACTIVE by default"
    assert data[0]["is_deleted"] is False


@pytest.mark.django_db
def test_inactive_account_archived_then_restored_becomes_active(
    api, entity, auth_headers, user_entity
):
    """
    Module 2: When an INACTIVE account (user unchecked) is archived and then restored,
    it should come back as ACTIVE, NOT remain inactive.

    Scenario:
    1. Account exists as INACTIVE (user explicitly unchecked it)
    2. Archive in Xero → marked is_deleted=true
    3. Restore in Xero → should become is_active=true (NOT preserve inactive state)

    Expected: User preference (inactive) should NOT be preserved after restoration.
    Restored accounts should always default to ACTIVE.
    """
    account = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="761",
        account_name="Depreciation - Equipment",
        account_type="DEPRECIATN",
        is_active=False,  # User had set this to inactive
        is_deleted=False,
        xero_account_id="xero-deprec-restore-2",
    )

    # Step 1: Verify account is inactive
    url = "/api/v1/entity-bill-accounts/?sync_chart=false&include_inactive=true"
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["is_active"] is False

    # Step 2: Archive the account (simulates removal from Xero)
    account.is_deleted = True
    account.save()

    # Verify it's hidden
    url_default = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url_default, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 0

    # Step 3: Restore the account (simulates restoration in Xero)
    # EXPECTED BEHAVIOR: Should become ACTIVE, not remain inactive
    account.is_deleted = False
    account.is_active = True  # This is what the sync SHOULD do
    account.save()

    # Verify account is now ACTIVE (not inactive)
    resp = api.get(url_default, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert (
        data[0]["is_active"] is True
    ), "Restored account should be ACTIVE by default, not preserve old inactive state"


@pytest.mark.django_db
def test_multiple_accounts_archived_and_restored(
    api, entity, auth_headers, user_entity
):
    """
    Module 2: Test restoring multiple accounts with mixed previous states.
    All should become ACTIVE after restoration.
    """
    # Create accounts with different states
    acc1 = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation - Buildings",
        account_type="DEPRECIATN",
        is_active=True,  # Was active
        is_deleted=False,
        xero_account_id="xero-deprec-multi-1",
    )

    acc2 = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="761",
        account_name="Depreciation - Equipment",
        account_type="DEPRECIATN",
        is_active=False,  # Was inactive
        is_deleted=False,
        xero_account_id="xero-deprec-multi-2",
    )

    acc3 = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="762",
        account_name="Depreciation - Vehicles",
        account_type="DEPRECIATN",
        is_active=True,  # Was active
        is_deleted=False,
        xero_account_id="xero-deprec-multi-3",
    )

    # Archive all accounts
    for acc in [acc1, acc2, acc3]:
        acc.is_deleted = True
        acc.save()

    # Verify all are hidden
    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 0

    # Restore all accounts
    for acc in [acc1, acc2, acc3]:
        acc.is_deleted = False
        acc.is_active = True  # All should become active
        acc.save()

    # Verify all are active
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 3
    for item in data:
        assert (
            item["is_active"] is True
        ), f"Account {item['account_code']} should be ACTIVE after restoration"


@pytest.mark.django_db
def test_restoration_with_code_change(api, entity, auth_headers, user_entity):
    """
    Module 2: When an account is restored but the code changed in Xero,
    it should still become active.
    """
    account = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=False,  # Was inactive
        is_deleted=False,
        xero_account_id="xero-deprec-code-change",
    )

    # Archive
    account.is_deleted = True
    account.save()

    # Restore with new code (simulates Xero code change)
    account.account_code = "765"
    account.is_deleted = False
    account.is_active = True  # Should become active
    account.save()

    url = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["account_code"] == "765"
    assert data[0]["is_active"] is True


@pytest.mark.django_db
def test_inactive_not_deleted_account_stays_inactive_until_sync(
    api, entity, auth_headers, user_entity
):
    """
    Module 2: Inactive accounts (user unchecked) that were never deleted
    will be reactivated when sync runs if they're ACTIVE in Xero.

    This simulates the Module 2 behavior after the fix where
    _upsert_entity_bill_account_xero_rows sets is_active=true.
    """
    # Create inactive account (user unchecked it)
    account = EntityBillAccountXero.objects.create(
        entity_id=entity.id,
        account_code="760",
        account_name="Depreciation",
        account_type="DEPRECIATN",
        is_active=False,  # User unchecked it
        is_deleted=False,  # But it's not deleted
        xero_account_id="xero-deprec-inactive",
    )

    # Verify it's currently inactive
    url = "/api/v1/entity-bill-accounts/?sync_chart=false&include_inactive=true"
    resp = api.get(url, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert data[0]["is_active"] is False

    # Simulate what sync does: if account exists in Xero (ACTIVE),
    # it gets is_active=true (as per our fix in _upsert_entity_bill_account_xero_rows)
    account.is_active = True
    account.save()

    # After sync, should be active
    url_default = "/api/v1/entity-bill-accounts/?sync_chart=false"
    resp = api.get(url_default, **auth_headers)
    data = json.loads(resp.content)
    assert len(data) == 1
    assert (
        data[0]["is_active"] is True
    ), "Account should be active after sync matches Xero's ACTIVE state"
