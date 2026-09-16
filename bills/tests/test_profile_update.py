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


@pytest.mark.django_db
class TestUpdateProfileEmailIdentity:
    """The email is half of how an account is identified, and the two halves live in
    different columns: Minty signs in on ``username``, password reset looks up ``email``.
    Every case below is a way the account could otherwise be locked out."""

    def _put(self, api_client, auth_headers, **fields):
        payload = {"email": "x@minty.com", "first_name": "A", "last_name": "B"}
        payload.update(fields)
        return api_client.put(
            "/api/v1/profile/me",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_headers,
        )

    def test_username_follows_the_email_when_it_was_the_email(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """Registration sets username FROM the email, so it has to move with it —
        otherwise the account signs in under the old address forever."""
        test_user.username = test_user.email
        test_user.save()

        resp = self._put(api_client, auth_headers, email="moved@minty.com")

        assert resp.status_code == 200
        test_user.refresh_from_db()
        assert test_user.email == "moved@minty.com"
        assert test_user.username == "moved@minty.com"

    def test_a_separate_login_handle_is_left_alone(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """A username the user chose separately is a working sign-in. Rewriting it to
        chase an email change would break it to fix nothing."""
        assert test_user.username == "testuser"

        resp = self._put(api_client, auth_headers, email="other@minty.com")

        assert resp.status_code == 200
        test_user.refresh_from_db()
        assert test_user.email == "other@minty.com"
        assert test_user.username == "testuser"

    def test_an_email_another_account_holds_is_refused(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """Both columns are UNIQUE, so without this the write is an IntegrityError and a
        500 rather than something the form can show against the field."""
        from shared_models.models import User

        User.objects.create(
            id="34f91ee7-b7c2-5108-b5fd-4f1166fec43b",
            email="taken@minty.com",
            password="hashed_pw",
            first_name="Someone",
            last_name="Else",
            username="someoneelse",
        )

        resp = self._put(api_client, auth_headers, email="taken@minty.com")

        assert resp.status_code == 422
        assert "already in use" in resp.json()["detail"]
        test_user.refresh_from_db()
        assert test_user.email == "test@minty.com"

    def test_a_username_another_account_holds_is_refused(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """The collision can be on the OTHER column: this account's username tracks its
        email, and the new address is already somebody's login handle."""
        from shared_models.models import User

        test_user.username = test_user.email
        test_user.save()
        User.objects.create(
            id="136cf2ea-2314-5a89-ae75-da4ea58e6257",
            email="different@minty.com",
            password="hashed_pw",
            first_name="Someone",
            last_name="Else",
            username="handle@minty.com",
        )

        resp = self._put(api_client, auth_headers, email="handle@minty.com")

        assert resp.status_code == 422
        test_user.refresh_from_db()
        assert test_user.email == "test@minty.com"
        assert test_user.username == "test@minty.com"

    def test_keeping_your_own_email_is_not_a_collision(
        self, api_client, auth_headers, test_user_entity, test_user
    ):
        """The common case — renaming yourself and leaving the address alone. Matching
        your own row must not read as "already in use"."""
        resp = self._put(
            api_client,
            auth_headers,
            email=test_user.email,
            first_name="Renamed",
        )

        assert resp.status_code == 200
        test_user.refresh_from_db()
        assert test_user.first_name == "Renamed"
        assert test_user.email == "test@minty.com"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "nope", "two@@at.com", "has space@x.com", "@nolocal.com"]
    )
    def test_an_unreachable_address_is_refused(
        self, api_client, auth_headers, test_user_entity, test_user, bad
    ):
        """An address that can never receive a password-reset mail locks the account out
        just as effectively as somebody else's."""
        resp = self._put(api_client, auth_headers, email=bad)

        assert resp.status_code == 422
        test_user.refresh_from_db()
        assert test_user.email == "test@minty.com"
