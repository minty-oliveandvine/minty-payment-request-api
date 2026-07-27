"""Tests for Xero OAuth refresh parity with Module 1 Flask."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
import requests
from django.utils import timezone as django_tz

from bills.services.xero_publish_service import _upload_file_to_xero_files_api
from bills.services.xero_token_service import (
    ensure_valid_token_persist,
    resolve_xero_access_token_for_entity,
)
from core.exceptions import BillValidationError


@pytest.mark.django_db
class TestEnsureValidTokenPersist:
    def test_skips_refresh_when_expiry_fields_missing(self, test_user):
        """Missing expires_in / token_created_at → treated as expired, refresh attempted."""
        test_user.access_token = "at"
        test_user.refresh_token = "rt"
        test_user.expires_in = None
        test_user.token_created_at = None
        test_user.save()

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "access_token": "refreshed-at",
            "refresh_token": "refreshed-rt",
            "expires_in": 1800,
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ) as mock_post:
            result = ensure_valid_token_persist(test_user)
            assert result is True
            mock_post.assert_called_once()

    def test_refreshes_when_expired_and_persists(self, test_user):
        test_user.access_token = "old-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=2)
        test_user.save()

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "access_token": "new-at",
            "refresh_token": "new-rt",
            "expires_in": 1800,
            "id_token": "idt",
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ):
            assert ensure_valid_token_persist(test_user) is True

        test_user.refresh_from_db()
        assert test_user.access_token == "new-at"
        assert test_user.refresh_token == "new-rt"


@pytest.mark.django_db
class TestResolveAccessTokenForEntity:
    def test_resolves_expired_token_via_token_service(
        self, test_entity, test_user, test_user_entity
    ):
        """An expired stored token is replaced by one fetched from the Flask token service."""
        test_entity.status = "connected"
        test_entity.xero_org_id = "org-abc"
        test_entity.save()

        test_user.access_token = "old-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=2)
        test_user.save()

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "access_token": "resolved-at",
            "xero_org_id": "org-abc",
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ):
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "resolved-at"

    # TC-XERO-002: token expired, token service supplies a fresh one, publish proceeds
    def test_tc_xero_002_expired_token_service_supplies_fresh(
        self, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-002: Expired token is replaced by the token service's value.

        Billing does not refresh; the Flask app does, behind an advisory lock.
        """
        test_entity.xero_org_id = "org-tc002"
        test_entity.save()

        test_user.xero_entity_id = "org-tc002"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "valid-rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "access_token": "fresh-at",
            "xero_org_id": "org-tc002",
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ):
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "fresh-at"

    # TC-XERO-003: token service cannot produce a token, raises with clear message
    def test_tc_xero_003_token_service_failure_raises(
        self, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-003: When the token service errors (500), billing raises rather than
        falling back to the stale token still sitting in the row."""
        test_entity.xero_org_id = "org-tc003"
        test_entity.save()

        test_user.xero_entity_id = "org-tc003"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "expired-rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        fake_500 = MagicMock()
        fake_500.status_code = 500
        fake_500.text = "internal error"

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake_500,
        ):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to xero" in str(exc_info.value).lower()

    # TC-XERO-010: expired local token → billing asks the Flask token service
    def test_tc_xero_010_expired_token_fetched_from_flask_service(
        self, settings, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-010: When the stored token is expired, billing requests one from the
        Flask token service and uses it. Billing never contacts Xero itself."""
        test_entity.xero_org_id = "org-tc010"
        test_entity.save()

        test_user.xero_entity_id = "org-tc010"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        service_resp = MagicMock()
        service_resp.status_code = 200
        service_resp.json.return_value = {
            "access_token": "flask-issued-at",
            "xero_org_id": "org-tc010",
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=service_resp,
        ) as mock_post:
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "flask-issued-at"

        # Exactly one call, to the token service — never to Xero.
        assert mock_post.call_count == 1
        called_url = mock_post.call_args.args[0]
        assert "identity.xero.com" not in called_url
        assert called_url == settings.XERO_TOKEN_SERVICE_URL

    # TC-XERO-015: the service assertion is signed, scoped, and bound to the entity
    def test_tc_xero_015_service_assertion_is_scoped_and_entity_bound(
        self, settings, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-015: entity_id travels inside the signed claims, so a leaked assertion
        cannot be replayed against a different entity."""
        test_entity.xero_org_id = "org-tc015"
        test_entity.save()

        test_user.xero_entity_id = "org-tc015"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        service_resp = MagicMock()
        service_resp.status_code = 200
        service_resp.json.return_value = {
            "access_token": "at",
            "xero_org_id": "org-tc015",
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=service_resp,
        ) as mock_post:
            resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        header = mock_post.call_args.kwargs["headers"]["Authorization"]
        assert header.startswith("Bearer ")
        claims = pyjwt.decode(header[7:], settings.SECRET_KEY, algorithms=["HS256"])
        assert claims["entity_id"] == test_entity.id
        assert claims["scope"] == "xero-access-token"
        assert "exp" in claims

    # TC-XERO-016: the token service says the connection needs re-establishing
    def test_tc_xero_016_service_reconnect_required_raises(
        self, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-016: A 409 from the token service means no usable token exists."""
        test_entity.xero_org_id = "org-tc016"
        test_entity.save()

        test_user.xero_entity_id = "org-tc016"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        service_resp = MagicMock()
        service_resp.status_code = 409
        service_resp.text = '{"status":"reconnect_required"}'

        with patch(
            "bills.services.xero_token_service.requests.post", return_value=service_resp
        ):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to xero" in str(exc_info.value).lower()

    # TC-XERO-017: token service unreachable must not become a 500 on the publish path
    def test_tc_xero_017_service_unreachable_raises_validation_error(
        self, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-017: If the Flask app is down, billing surfaces a reconnect prompt rather
        than letting a RequestException escape as an unhandled 500."""
        test_entity.xero_org_id = "org-tc017"
        test_entity.save()

        test_user.xero_entity_id = "org-tc017"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        with patch(
            "bills.services.xero_token_service.requests.post",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to xero" in str(exc_info.value).lower()

    # TC-XERO-012: a present-but-expired access_token must never be handed to Xero
    def test_tc_xero_012_expired_token_is_never_returned(
        self, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-012: Regression. Billing checked `if user.access_token:` — presence, not
        validity — and returned an expired token, which Xero rejects with 403
        AuthenticationUnsuccessful instead of a clean reconnect prompt."""
        test_entity.xero_org_id = "org-tc012"
        test_entity.save()

        test_user.xero_entity_id = "org-tc012"
        test_user.access_token = "expired-but-present"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        fake_400 = MagicMock()
        fake_400.status_code = 400
        fake_400.text = "invalid_grant"

        with patch(
            "bills.services.xero_token_service.requests.post", return_value=fake_400
        ):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to xero" in str(exc_info.value).lower()

    # TC-XERO-013: billing must never call Xero's token endpoint, whatever happens
    def test_tc_xero_013_never_calls_xero_identity(
        self, settings, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-013: Even with client credentials present, billing must not POST to
        identity.xero.com. Xero's refresh tokens are single-use; a second refresher would
        brick the connection. Billing goes through the Flask token service or fails."""
        settings.XERO_CLIENT_ID = "should-never-be-used"
        settings.XERO_CLIENT_SECRET = "should-never-be-used"

        test_entity.xero_org_id = "org-tc013"
        test_entity.save()

        test_user.xero_entity_id = "org-tc013"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        service_resp = MagicMock()
        service_resp.status_code = 409
        service_resp.text = '{"status":"reconnect_required"}'

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=service_resp,
        ) as mock_post:
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        for call in mock_post.call_args_list:
            assert "identity.xero.com" not in call.args[0]
        assert "reconnect to xero" in str(exc_info.value).lower()

    # TC-XERO-014: production happy path — billing rides on the token the Flask app refreshed
    def test_tc_xero_014_no_credentials_valid_token_is_used(
        self, settings, test_entity, test_user, test_user_entity
    ):
        """TC-XERO-014: With no client credentials and a still-valid token, billing returns it
        without contacting Xero. This is the normal production path."""
        settings.XERO_CLIENT_ID = ""
        settings.XERO_CLIENT_SECRET = ""

        test_entity.xero_org_id = "org-tc014"
        test_entity.save()

        test_user.xero_entity_id = "org-tc014"
        test_user.access_token = "flask-issued-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now()
        test_user.save()

        with patch("bills.services.xero_token_service.requests.post") as mock_post:
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        mock_post.assert_not_called()
        assert token == "flask-issued-at"

    # TC-XERO-011: Files API upload with empty token uses guard, does not send any HTTP request
    def test_tc_xero_011_attachment_upload_empty_token_guard(self):
        """TC-XERO-011: _upload_file_to_xero_files_api returns None immediately when access_token is empty."""
        with patch("bills.services.xero_publish_service.requests.post") as mock_post:
            result = _upload_file_to_xero_files_api(
                access_token="",
                xero_org_id="org-x",
                filename="test.pdf",
                file_bytes=b"data",
                content_type="application/pdf",
            )

        assert result is None
        mock_post.assert_not_called()
