"""POST /api/v1/entity-bill-contacts/ — create Xero contact + DB sync."""

from unittest.mock import MagicMock, patch

import pytest

from bills.tests.conftest import give_xero_token

from shared_models.models import XeroContactSync


@pytest.mark.django_db
class TestEntityBillContactsCreate:
    def test_requires_bill_role(
        self, api_client, auth_token, test_entity, test_user_entity
    ):
        test_user_entity.role = "guest"
        test_user_entity.save(update_fields=["role"])
        try:
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "New Vendor"},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {auth_token}",
                HTTP_X_ENTITY_ID=test_entity.id,
            )
            assert resp.status_code == 403
        finally:
            test_user_entity.role = "admin"
            test_user_entity.save(update_fields=["role"])

    def test_rejects_empty_name(self, api_client, auth_headers, test_user_entity):
        resp = api_client.post(
            "/api/v1/entity-bill-contacts/",
            data={"name": "   "},
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "contact name" in resp.json()["detail"].lower()

    def test_requires_xero_connected_entity(
        self,
        api_client,
        auth_headers,
        test_entity,
        test_user_entity,
    ):
        test_entity.status = "active"
        test_entity.xero_org_id = None
        test_entity.save()
        resp = api_client.post(
            "/api/v1/entity-bill-contacts/",
            data={"name": "Acme Ltd"},
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "connected" in resp.json()["detail"].lower()

    def test_creates_contact_and_db_row(
        self,
        db,
        api_client,
        auth_headers,
        test_user,
        test_entity,
        test_user_entity,
    ):
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        give_xero_token(test_user, "fake-xero-token")

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "Contacts": [
                {"ContactID": "new-cid-001", "Name": "Fresh Supplier Co"},
            ]
        }

        with patch(
            "bills.services.contact_service.requests.post",
            return_value=fake_resp,
        ) as mock_post:
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "Fresh Supplier Co"},
                content_type="application/json",
                **auth_headers,
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["xero_contact_id"] == "new-cid-001"
        assert body["name"] == "Fresh Supplier Co"
        assert body["entity_id"] == test_entity.id
        mock_post.assert_called_once()

        row = XeroContactSync.objects.get(
            entity_id=test_entity.id,
            xero_contact_id="new-cid-001",
        )
        assert row.name == "Fresh Supplier Co"
        assert row.xero_org_id == "org-abc"
        assert row.category is None

    def test_xero_error_status_maps_to_422(
        self,
        db,
        api_client,
        auth_headers,
        test_user,
        test_entity,
        test_user_entity,
    ):
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        give_xero_token(test_user, "fake-xero-token")

        fake_resp = MagicMock()
        fake_resp.status_code = 400
        fake_resp.json.return_value = {"Detail": "Name already in use"}
        fake_resp.text = "bad"

        with patch(
            "bills.services.contact_service.requests.post",
            return_value=fake_resp,
        ):
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "Dup"},
                content_type="application/json",
                **auth_headers,
            )

        assert resp.status_code == 422
        assert "already in use" in resp.json()["detail"]
