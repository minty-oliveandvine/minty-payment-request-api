import json

import pytest


@pytest.mark.django_db
class TestUpdateProfile:
    def test_update_profile_success(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """Test successful profile update with valid data."""
        payload = {
            "email": "updated@minty.com",
            "first_name": "Updated",
            "last_name": "Name",
        }

        resp = api_client.put(
            "/api/v1/profile/me",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "updated@minty.com"
        assert body["first_name"] == "Updated"
        assert body["last_name"] == "Name"
        assert body["id"] == test_user.id
        assert body["username"] == test_user.username

        test_user.refresh_from_db()
        assert test_user.email == "updated@minty.com"
        assert test_user.first_name == "Updated"
        assert test_user.last_name == "Name"

    def test_update_profile_trims_whitespace(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """Test that profile update trims leading/trailing whitespace."""
        payload = {
            "email": "  trimmed@minty.com  ",
            "first_name": "  Trimmed  ",
            "last_name": "  Whitespace  ",
        }

        resp = api_client.put(
            "/api/v1/profile/me",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "trimmed@minty.com"
        assert body["first_name"] == "Trimmed"
        assert body["last_name"] == "Whitespace"

    def test_update_profile_requires_auth(self, api_client):
        """Test that profile update requires authentication."""
        payload = {
            "email": "nope@minty.com",
            "first_name": "Should",
            "last_name": "Fail",
        }

        resp = api_client.put(
            "/api/v1/profile/me",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert resp.status_code == 401

    def test_update_profile_all_fields_required(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """Test that all fields must be provided."""
        payload = {
            "email": "incomplete@minty.com",
        }

        resp = api_client.put(
            "/api/v1/profile/me",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )

        assert resp.status_code == 422
