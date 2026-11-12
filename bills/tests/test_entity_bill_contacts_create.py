"""POST /api/v1/entity-bill-contacts/ — create Xero contact + DB sync."""

from unittest.mock import MagicMock, patch

import pytest
from minty_db.models import XeroContactSync

from bills.tests.conftest import give_xero_token


@pytest.mark.django_db
class TestEntityBillContactsCreate:
    def test_requires_bill_role(
        self, api_client, auth_token, test_entity, test_user_entity
    ):
        test_user_entity.role = "entity_base"  # the lowest entity_role: no bill permissions
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

    @pytest.mark.parametrize("length", [101, 150])
    def test_rejects_name_over_100(self, api_client, auth_headers, test_user_entity, length):
        """Bill.contact_name holds 100, so a longer new supplier is refused before Xero."""
        resp = api_client.post(
            "/api/v1/entity-bill-contacts/",
            data={"name": "A" * length},
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Keep the supplier's name to 100 characters or fewer."

    def test_without_xero_the_supplier_is_the_companys_own(
        self,
        api_client,
        auth_headers,
        test_entity,
        test_user_entity,
    ):
        """Contacts are never blocked behind Xero (owner, 2026-11-12): a Minty row, no Xero call;
        the next live sync puts it in Xero."""
        test_entity.status = "disconnected"
        test_entity.xero_org_id = None
        test_entity.save()
        with patch("bills.services.contact_service.requests.post") as mock_post:
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "Acme Ltd"},
                content_type="application/json",
                **auth_headers,
            )
        assert resp.status_code == 201, resp.json()
        row = XeroContactSync.objects.get(entity_id=test_entity.id)
        assert (row.name, row.xero_contact_id, row.source) == ("Acme Ltd", None, "user")
        assert resp.json()["xero_contact_id"] == str(row.id)
        mock_post.assert_not_called()

    def test_a_name_the_company_has_is_that_supplier(
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
        XeroContactSync.objects.create(
            entity_id=test_entity.id, xero_contact_id="cid-acme", xero_org_id="org-abc", name="Acme  Ltd"
        )
        with patch("bills.services.contact_service.requests.post") as mock_post:
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "ACME LTD"},
                content_type="application/json",
                **auth_headers,
            )
        assert resp.status_code == 201 and resp.json()["xero_contact_id"] == "cid-acme"
        assert XeroContactSync.objects.filter(entity_id=test_entity.id).count() == 1
        mock_post.assert_not_called()

    def test_a_live_company_links_xeros_contact_of_that_name(
        self,
        db,
        api_client,
        auth_headers,
        test_user,
        test_entity,
        test_user_entity,
    ):
        from minty_db.models import XeroContact

        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        give_xero_token(test_user, "fake-xero-token")
        XeroContact.objects.create(
            entity_id=test_entity.id, xero_org_id="org-abc", xero_guid="cid-kept", name="Acme Ltd", status="ACTIVE"
        )
        with patch("bills.services.contact_service.requests.post") as mock_post:
            resp = api_client.post(
                "/api/v1/entity-bill-contacts/",
                data={"name": "acme ltd"},
                content_type="application/json",
                **auth_headers,
            )
        assert resp.status_code == 201 and resp.json()["xero_contact_id"] == "cid-kept"
        mock_post.assert_not_called()

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
        assert body["entity_id"] == str(test_entity.id)
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


@pytest.mark.django_db
class TestADeletedSupplierComesBack:
    """Owner, 2026-11-12: a supplier the company deleted, added again, is that one brought back -
    ACTIVE in Xero when it is there - never a second one."""

    def _connected(self, test_entity, test_user):
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()
        give_xero_token(test_user, "fake-xero-token")

    def _add(self, api_client, auth_headers, name):
        return api_client.post(
            "/api/v1/entity-bill-contacts/", data={"name": name}, content_type="application/json", **auth_headers
        )

    def test_a_deleted_one_in_xero_is_active_again_there(
        self, api_client, auth_headers, test_user, test_entity, test_user_entity
    ):
        self._connected(test_entity, test_user)
        gone = XeroContactSync.objects.create(
            entity_id=test_entity.id, name="Acme Ltd", xero_contact_id="cid-acme", xero_org_id="org-abc",
            record_status="archived",
        )
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"Contacts": [{"ContactID": "cid-acme", "Name": "Acme Ltd"}]}
        with patch("bills.services.contact_service.requests.post", return_value=ok) as mock_post:
            resp = self._add(api_client, auth_headers, "acme ltd")
        assert resp.status_code == 201 and resp.json()["xero_contact_id"] == "cid-acme"
        assert mock_post.call_args[1]["json"] == {"Contacts": [{"ContactID": "cid-acme", "ContactStatus": "ACTIVE"}]}
        gone.refresh_from_db()
        assert gone.record_status == "active"

    def test_xeros_archived_contact_of_that_name_is_linked_again(
        self, api_client, auth_headers, test_user, test_entity, test_user_entity
    ):
        from minty_db.models import XeroContact

        self._connected(test_entity, test_user)
        XeroContact.objects.create(
            entity_id=test_entity.id, xero_org_id="org-abc", xero_guid="cid-old", name="Acme Ltd", status="ARCHIVED"
        )
        ok = MagicMock(status_code=200)
        ok.json.return_value = {"Contacts": [{"ContactID": "cid-old", "Name": "Acme Ltd"}]}
        with patch("bills.services.contact_service.requests.post", return_value=ok) as mock_post:
            resp = self._add(api_client, auth_headers, "Acme Ltd")
        assert resp.status_code == 201 and resp.json()["xero_contact_id"] == "cid-old"
        assert mock_post.call_args[1]["json"] == {"Contacts": [{"ContactID": "cid-old", "ContactStatus": "ACTIVE"}]}
        assert XeroContact.objects.get(xero_guid="cid-old").status == "ACTIVE"

    def test_offline_a_minty_one_comes_back_and_one_in_xero_waits(
        self, api_client, auth_headers, test_entity, test_user_entity
    ):
        test_entity.status = "disconnected"
        test_entity.save()
        own = XeroContactSync.objects.create(entity_id=test_entity.id, name="Acme Ltd", record_status="archived")
        XeroContactSync.objects.create(
            entity_id=test_entity.id, name="Sam's Fish", xero_contact_id="cid-sam", record_status="archived"
        )
        with patch("bills.services.contact_service.requests.post") as mock_post:
            back = self._add(api_client, auth_headers, "Acme Ltd")
            waits = self._add(api_client, auth_headers, "Sam's Fish")
        assert back.status_code == 201 and back.json()["xero_contact_id"] == str(own.id)
        assert waits.status_code == 422 and "reconnect Xero to bring it back" in waits.json()["detail"]
        mock_post.assert_not_called()
