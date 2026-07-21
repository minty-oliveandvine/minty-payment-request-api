"""
Verify the bill contacts endpoint now matches Module 1 behaviour:
  - DB fallback returns ALL contacts for the entity (no xero_org_id filter).
  - Contacts with NULL/empty xero_org_id are included.
"""

import pytest
from unittest.mock import patch, MagicMock

from django.utils import timezone as django_tz

from shared_models.models import User, XeroContactSync


@pytest.fixture
def _seed_contacts(db, test_entity):
    """Seed xero_contact_sync with contacts covering all xero_org_id states."""
    XeroContactSync.objects.create(
        id="c1",
        entity_id=test_entity.id,
        xero_contact_id="xero-001",
        xero_org_id="org-abc",
        name="Alpha Supplies",
        category="SUPPLIER",
    )
    XeroContactSync.objects.create(
        id="c2",
        entity_id=test_entity.id,
        xero_contact_id="xero-002",
        xero_org_id="org-abc",
        name="Beta Corp",
        category="SUPPLIER",
    )
    XeroContactSync.objects.create(
        id="c3",
        entity_id=test_entity.id,
        xero_contact_id="xero-003",
        xero_org_id=None,
        name="Gamma Ltd (no org id)",
        category="SUPPLIER",
    )
    XeroContactSync.objects.create(
        id="c4",
        entity_id=test_entity.id,
        xero_contact_id="xero-004",
        xero_org_id="",
        name="Delta Inc (empty org id)",
        category="SUPPLIER",
    )
    XeroContactSync.objects.create(
        id="c5",
        entity_id=test_entity.id,
        xero_contact_id="xero-005",
        xero_org_id="org-abc",
        name="Epsilon Trading",
        category=None,
    )


@pytest.mark.django_db
class TestContactListParity:
    """Confirm the bill contacts endpoint returns the same list as Module 1."""

    def test_db_fallback_returns_all_contacts_including_missing_org_id(
        self, api_client, auth_headers, test_user_entity, _seed_contacts,
    ):
        """When entity is disconnected, all DB contacts are returned."""
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Alpha Supplies" in names
        assert "Beta Corp" in names
        assert "Gamma Ltd (no org id)" in names
        assert "Delta Inc (empty org id)" in names
        assert "Epsilon Trading" in names

    def test_db_fallback_returns_all_five_contacts(
        self, api_client, auth_headers, test_user_entity, _seed_contacts,
    ):
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_contacts_sorted_alphabetically(
        self, api_client, auth_headers, test_user_entity, _seed_contacts,
    ):
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        names = [c["name"] for c in resp.json()]
        assert names == sorted(names, key=str.lower)

    def test_response_shape(
        self, api_client, auth_headers, test_user_entity, _seed_contacts,
    ):
        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        contact = resp.json()[0]
        assert "id" in contact
        assert "entity_id" in contact
        assert "xero_contact_id" in contact
        assert "name" in contact

    def test_xero_live_fetch_used_when_connected(
        self, db, api_client, auth_headers, test_user, test_entity, test_user_entity,
    ):
        """When entity is connected and user has token, Xero API is called."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = "fake-xero-token"
        test_user.save()

        fake_xero_response = MagicMock()
        fake_xero_response.status_code = 200
        fake_xero_response.json.return_value = {
            "Contacts": [
                {"ContactID": "xero-live-001", "Name": "Live Contact A"},
                {"ContactID": "xero-live-002", "Name": "Live Contact B"},
            ]
        }

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=fake_xero_response,
        ):
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Live Contact A" in names
        assert "Live Contact B" in names

    def test_falls_back_to_db_when_xero_fails(
        self, db, api_client, auth_headers, test_user, test_entity,
        test_user_entity, _seed_contacts,
    ):
        """When Xero API fails, falls back to DB with all contacts."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = "fake-xero-token"
        test_user.save()

        fake_xero_response = MagicMock()
        fake_xero_response.status_code = 500
        fake_xero_response.text = "Internal Server Error"

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=fake_xero_response,
        ):
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert len(names) == 5
        assert "Gamma Ltd (no org id)" in names
        assert "Delta Inc (empty org id)" in names

    def test_falls_back_to_db_when_no_access_token(
        self, db, api_client, auth_headers, test_user, test_entity,
        test_user_entity, _seed_contacts,
    ):
        """When no usable Xero token (JWT empty and no org owner), falls back to DB."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = None
        test_user.save()

        resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_live_xero_uses_org_owner_token_when_jwt_has_none(
        self, db, api_client, auth_headers, test_user, test_entity,
        test_user_entity, _seed_contacts,
    ):
        """Match Flask: org-linked user supplies Xero token when JWT user has none."""
        User.objects.create(
            id="owner-user-xyz",
            email="owner_xyz@minty.com",
            password="x",
            first_name="O",
            last_name="wner",
            username="ownerxyz",
            system_role="user",
            xero_entity_id="org-abc",
            access_token="owner-only-token",
            expires_in=1800,
            token_created_at=django_tz.now(),
        )
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = None
        test_user.save()

        fake_xero_response = MagicMock()
        fake_xero_response.status_code = 200
        fake_xero_response.json.return_value = {
            "Contacts": [
                {"ContactID": "from-owner", "Name": "Via Owner Token"},
            ]
        }

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=fake_xero_response,
        ) as mock_get:
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        assert any(c["name"] == "Via Owner Token" for c in resp.json())
        mock_get.assert_called_once()
        auth_hdr = mock_get.call_args.kwargs["headers"]["Authorization"]
        assert auth_hdr == "Bearer owner-only-token"

    def test_live_xero_merges_db_row_not_yet_in_xero_get(
        self, db, api_client, auth_headers, test_user, test_entity, test_user_entity,
    ):
        """Live GET can lag; xero_contact_sync row must still appear in the list."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        test_user.access_token = "fake-xero-token"
        test_user.save()

        XeroContactSync.objects.create(
            id="sync-not-in-live-yet",
            entity_id=test_entity.id,
            xero_contact_id="xero-just-posted",
            xero_org_id="org-abc",
            name="Fresh From POST",
            category=None,
        )

        fake_xero_response = MagicMock()
        fake_xero_response.status_code = 200
        fake_xero_response.json.return_value = {
            "Contacts": [
                {"ContactID": "xero-already-in-xero", "Name": "Already In Xero"},
            ]
        }

        with patch(
            "bills.services.contact_service.requests.get",
            return_value=fake_xero_response,
        ):
            resp = api_client.get("/api/v1/entity-bill-contacts/", **auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        by_id = {c["xero_contact_id"]: c for c in body}
        assert set(by_id) == {"xero-already-in-xero", "xero-just-posted"}
        assert by_id["xero-just-posted"]["name"] == "Fresh From POST"
