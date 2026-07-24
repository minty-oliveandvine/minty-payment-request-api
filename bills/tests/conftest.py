import jwt as pyjwt
import pytest
import requests
from django.conf import settings
from django.test import Client
from django.utils import timezone as django_tz

from bills.models import Attachment, Bill, BillAttachment, BillLineItem
from shared_models.models import Entity, User, UserEntity


@pytest.fixture(autouse=True)
def _block_real_token_service_calls(monkeypatch):
    """Stop the suite reaching a live Flask app over HTTP.

    `resolve_xero_access_token_for_entity` POSTs to the Flask token service whenever the
    stored token is expired or absent. Unblocked, the suite talks to whatever is
    listening on FLASK_APP_URL — on a developer machine that is a running Minty against
    a real database, and that endpoint can spend a single-use Xero refresh token.

    Tests that exercise the token service patch `requests.post` themselves; those
    patches are applied inside the test and take precedence over this one.
    """
    def _blocked(*args, **kwargs):
        raise requests.ConnectionError("real HTTP blocked in tests")

    monkeypatch.setattr("bills.services.xero_token_service.requests.post", _blocked)


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def test_user(db):
    # The Flask app always writes access_token, refresh_token, expires_in and
    # token_created_at together, so a token with unknown expiry never occurs in
    # practice. Tests that set access_token must inherit valid expiry metadata,
    # otherwise `_token_expired` treats the token as expired and callers refuse it.
    return User.objects.create(
        id="test-user-001",
        email="test@minty.com",
        password="hashed_pw",
        first_name="Test",
        last_name="User",
        username="testuser",
        system_role="user",
        expires_in=1800,
        token_created_at=django_tz.now(),
    )


@pytest.fixture
def test_entity(db):
    return Entity.objects.create(
        id="test-entity-001",
        name="Test Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="active",
    )


@pytest.fixture
def test_user_entity(db, test_user, test_entity):
    return UserEntity.objects.create(
        user=test_user,
        entity=test_entity,
        role="admin",
    )


@pytest.fixture
def auth_token(test_user, test_entity):
    return pyjwt.encode(
        {"user_id": test_user.id, "entity_id": test_entity.id},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


@pytest.fixture
def auth_headers(auth_token, test_entity):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {auth_token}",
        "HTTP_X_ENTITY_ID": test_entity.id,
    }


@pytest.fixture
def draft_bill(db, test_user, test_entity):
    return Bill.objects.create(
        entity_id=test_entity.id,
        contact="Vendor A",
        status="draft",
        amount=100.00,
        description="Test bill",
        uploaded_by=test_user.id,
    )


@pytest.fixture
def draft_bill_with_attachment(db, draft_bill):
    attachment = Attachment.objects.create(
        original_name="invoice.pdf",
        stored_name="stored.pdf",
        file_path="attachments/test/stored.pdf",
        mime_type="application/pdf",
        file_size=1024,
        file_extension="pdf",
        storage_provider="s3",
        uploaded_by=draft_bill.uploaded_by,
    )
    BillAttachment.objects.create(
        bill=draft_bill,
        attachment=attachment,
        attachment_role="invoice",
        created_by=draft_bill.uploaded_by,
    )
    return draft_bill
