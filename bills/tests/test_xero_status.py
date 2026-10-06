"""GET /api/v1/auth/xero-status: whether the token's COMPANY is live on Xero.

It used to read ``user.refresh_token`` - a column that moved to ``user_token`` - and swallowed
the error, so it answered "not connected" for everyone. Payment Request Settings hides its
account codes on that answer, so it must be the company's connection, read from the database.
"""

from __future__ import annotations

import pytest

from bills.tests.conftest import give_xero_token
from shared_models.models import User

URL = "/api/v1/auth/xero-status"


def _connect(entity, *, by=None, status="connected"):
    entity.xero_org_id = "org-live-1"
    entity.status = status
    entity.connected_by_user_id = by.id if by else None
    entity.save()


def _status(api_client, auth_headers):
    response = api_client.get(URL, **auth_headers)
    assert response.status_code == 200
    return response.json()["connected"]


@pytest.mark.django_db
def test_a_company_with_no_xero_org_is_not_connected(api_client, auth_headers, test_user, test_user_entity):
    give_xero_token(test_user)  # the person's own token does not make the company live

    assert _status(api_client, auth_headers) is False


@pytest.mark.django_db
def test_a_company_marked_disconnected_is_not_connected(api_client, auth_headers, test_entity, test_user,
                                                         test_user_entity):
    _connect(test_entity, by=test_user, status="disconnected")  # revoked in Xero
    give_xero_token(test_user)

    assert _status(api_client, auth_headers) is False


@pytest.mark.django_db
def test_a_company_whose_token_has_no_refresh_token_is_not_connected(api_client, auth_headers, test_entity,
                                                                     test_user, test_user_entity):
    _connect(test_entity, by=test_user)
    give_xero_token(test_user, refresh_token=None)

    assert _status(api_client, auth_headers) is False


@pytest.mark.django_db
def test_a_company_with_its_connectors_token_is_connected(api_client, auth_headers, test_entity, test_user,
                                                          test_user_entity):
    connector = User.objects.create(
        id="5b0f5c8e-2d8a-5f43-9d1c-6f0b6b1f2a10", email="connector@minty.com", password="x",
        first_name="Con", last_name="Nector", username="connector", system_role="normal",
    )
    _connect(test_entity, by=connector)
    give_xero_token(connector)  # the viewer holds no token of their own

    assert _status(api_client, auth_headers) is True


@pytest.mark.django_db
def test_xero_status_needs_a_role_on_the_company(api_client, auth_headers, test_entity, test_user):
    # no user_entity row: the company's connection is not the caller's to read
    _connect(test_entity, by=test_user)
    give_xero_token(test_user)

    assert api_client.get(URL, **auth_headers).status_code == 401
