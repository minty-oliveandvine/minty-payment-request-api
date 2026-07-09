"""Tests for Xero OAuth refresh parity with Module 1 Flask."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone as django_tz

from bills.services.xero_publish_service import _upload_file_to_xero_files_api
from bills.services.xero_token_service import (
    ensure_valid_token_persist,
    resolve_xero_access_token_for_entity,
)
from core.exceptions import BillValidationError
from shared_models.models import User


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
    def test_resolves_after_refresh(self, test_entity, test_user, test_user_entity):
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
            "refresh_token": "rt2",
            "expires_in": 1800,
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ):
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "resolved-at"

    # TC-XERO-002: token expired, auto-refresh succeeds, publish proceeds
    def test_tc_xero_002_expired_token_refresh_succeeds(self, test_entity, test_user, test_user_entity):
        """TC-XERO-002: Expired token triggers refresh; returned token is the refreshed value."""
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
            "refresh_token": "fresh-rt",
            "expires_in": 1800,
        }

        with patch(
            "bills.services.xero_token_service.requests.post",
            return_value=fake,
        ):
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "fresh-at"

    # TC-XERO-003: token expired, refresh token also expired, raises with clear message
    def test_tc_xero_003_expired_refresh_token_raises(self, test_entity, test_user, test_user_entity):
        """TC-XERO-003: When Xero rejects the refresh (400) and no access_token remains, raises."""
        test_entity.xero_org_id = "org-tc003"
        test_entity.save()

        # Simulate a user whose access_token was cleared after a prior failure
        # and whose refresh_token is now also expired.
        test_user.xero_entity_id = "org-tc003"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "expired-rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        fake_400 = MagicMock()
        fake_400.status_code = 400
        fake_400.text = "invalid_grant"

        def post_side_effect(*args, **kwargs):
            # Simulate the access_token being cleared by the refresh failure handler
            # (e.g. a prior cleanup job) before refresh_from_db is called.
            User.objects.filter(pk=test_user.pk).update(access_token="")
            return fake_400

        with patch(
            "bills.services.xero_token_service.requests.post",
            side_effect=post_side_effect,
        ):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to Xero" in str(exc_info.value).lower()

    # TC-XERO-010: concurrent refresh race — second caller recovers token from DB
    def test_tc_xero_010_concurrent_refresh_race_recovery(self, test_entity, test_user, test_user_entity):
        """TC-XERO-010: When refresh POST fails (race loser), re-read DB recovers winner's token.

        The winner writes both `access_token` and a fresh `token_created_at` — the
        latter is what makes the token usable. A winner that only wrote the token
        string would leave an expired row; see TC-XERO-012.
        """
        test_entity.xero_org_id = "org-tc010"
        test_entity.save()

        test_user.xero_entity_id = "org-tc010"
        test_user.access_token = "stale-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(hours=3)
        test_user.save()

        winner_token = "winner-at"

        def fake_post(*args, **kwargs):
            # Simulate race: refresh POST fails (400) but the winner already wrote
            # a new access_token — and stamped its creation time — to the DB row.
            User.objects.filter(pk=test_user.pk).update(
                access_token=winner_token,
                token_created_at=django_tz.now(),
            )
            resp = MagicMock()
            resp.status_code = 400
            resp.text = "invalid_grant"
            return resp

        with patch("bills.services.xero_token_service.requests.post", side_effect=fake_post):
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == winner_token

    # TC-XERO-012: refresh fails and nobody else refreshed — must not return the stale token
    def test_tc_xero_012_failed_refresh_never_returns_expired_token(
        self, test_entity, test_user, test_user_entity
    ):
        """A non-empty but expired access_token must raise, not be handed to Xero.

        Regression guard: the failure branch once checked only for a *present*
        access_token, so a consumed single-use refresh left the caller publishing
        with a token that was days past expiry.
        """
        test_entity.xero_org_id = "org-tc012"
        test_entity.save()

        test_user.xero_entity_id = "org-tc012"
        test_user.access_token = "consumed-at"  # present, but stale
        test_user.refresh_token = "already-consumed-rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now() - timedelta(days=2)
        test_user.save()

        fake_400 = MagicMock()
        fake_400.status_code = 400
        fake_400.text = "invalid_grant"

        with patch("bills.services.xero_token_service.requests.post", return_value=fake_400):
            with pytest.raises(BillValidationError) as exc_info:
                resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert "reconnect to xero" in str(exc_info.value).lower()

        # The stale token is still on the row — the guard, not absence, is what stopped it.
        test_user.refresh_from_db()
        assert test_user.access_token == "consumed-at"

    # TC-XERO-013: a live token is never refreshed and never re-POSTed
    def test_tc_xero_013_valid_token_skips_refresh(self, test_entity, test_user, test_user_entity):
        """TC-XERO-013: Unexpired token short-circuits before acquiring the lock or POSTing."""
        test_entity.xero_org_id = "org-tc013"
        test_entity.save()

        test_user.xero_entity_id = "org-tc013"
        test_user.access_token = "live-at"
        test_user.refresh_token = "rt"
        test_user.expires_in = 1800
        test_user.token_created_at = django_tz.now()
        test_user.save()

        with patch("bills.services.xero_token_service.requests.post") as mock_post:
            token = resolve_xero_access_token_for_entity(test_entity.id, test_user.id)

        assert token == "live-at"
        mock_post.assert_not_called()

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
