"""The Xero unique keys (minty-db v0.4, 2026-11-03) never answer 500 here.

``entity_bill_account_xero`` holds one row per Xero account per company
(``uq_ebax_entity_xero_account``, partial: hand-added codes with no Xero id are free) and
``xero_contact_sync`` one row per Xero contact per company (``uq_xcs_entity_contact``).
Flask's syncs write the same tables, so a create here can meet a row a sync stored a moment
before: the contact create takes that row as the contact, and a second bill code for the same
Xero account is refused (409) rather than duplicated.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError
from minty_db.models import XeroContactSync

from bills.models import EntityBillAccountXero
from bills.tests.conftest import give_xero_token

pytestmark = pytest.mark.django_db


def _add_code(api_client, auth_headers, **body):
    return api_client.post(
        "/api/v1/entity-bill-accounts/",
        data={"account_code": "400", "account_name": "Advertising", "account_type": "EXPENSE", **body},
        content_type="application/json",
        **auth_headers,
    )


def test_a_second_code_for_the_same_xero_account_is_refused(api_client, auth_headers, test_user_entity, test_entity):
    assert _add_code(api_client, auth_headers, xero_account_id="acc-400").status_code == 201

    again = _add_code(api_client, auth_headers, xero_account_id="acc-400")

    assert again.status_code == 409, again.content
    assert again.json()["detail"] == "That Xero account is already in your list of payment account codes."
    assert EntityBillAccountXero.objects.filter(entity_id=test_entity.id, xero_account_id="acc-400").count() == 1


def test_codes_added_by_hand_have_no_xero_id_and_any_number_may_exist(
    api_client, auth_headers, test_user_entity, test_entity
):
    assert _add_code(api_client, auth_headers, account_code="900").status_code == 201
    assert _add_code(api_client, auth_headers, account_code="901").status_code == 201
    assert EntityBillAccountXero.objects.filter(entity_id=test_entity.id, xero_account_id="").count() == 2


def test_a_code_racing_a_sync_is_refused_not_a_500(api_client, auth_headers, test_user_entity, test_entity):
    """The look-up said free, then Flask's sync stored the account: the insert meets the key."""
    with patch.object(
        EntityBillAccountXero.objects, "create",
        side_effect=IntegrityError('duplicate key value violates unique constraint "uq_ebax_entity_xero_account"'),
    ):
        resp = _add_code(api_client, auth_headers, xero_account_id="acc-400")

    assert resp.status_code == 409, resp.content


def test_a_contact_a_sync_stored_first_is_that_contact(
    api_client, auth_headers, test_user, test_entity, test_user_entity, monkeypatch
):
    test_entity.status = "connected"
    test_entity.xero_org_id = "org-abc"
    test_entity.save()
    give_xero_token(test_user, "fake-xero-token")
    XeroContactSync.objects.create(entity_id=test_entity.id, xero_contact_id="new-cid-001",
                                   xero_org_id="org-abc", name="From the sync")
    manager = XeroContactSync.objects
    real_filter, looked = manager.filter, []

    def before_the_sync(*args, **kwargs):  # the look-up ran before the sync committed
        found = real_filter(*args, **kwargs)
        if not looked:
            looked.append(True)
            return found.none()
        return found

    def meets_the_key(**_values):
        raise IntegrityError('duplicate key value violates unique constraint "uq_xcs_entity_contact"')

    monkeypatch.setattr(manager, "filter", before_the_sync)
    monkeypatch.setattr(manager, "create", meets_the_key)
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"Contacts": [{"ContactID": "new-cid-001", "Name": "Fresh Supplier Co"}]}
    with patch("bills.services.contact_service.requests.post", return_value=fake_resp):
        resp = api_client.post(
            "/api/v1/entity-bill-contacts/",
            data={"name": "Fresh Supplier Co"},
            content_type="application/json",
            **auth_headers,
        )

    assert resp.status_code == 201, resp.content
    assert resp.json()["xero_contact_id"] == "new-cid-001"
    monkeypatch.undo()
    rows = XeroContactSync.objects.filter(entity_id=test_entity.id, xero_contact_id="new-cid-001")
    assert [r.name for r in rows] == ["Fresh Supplier Co"]
