import uuid

import jwt as pyjwt
import pytest
import requests
from django.conf import settings
from django.test import Client
from django.utils import timezone as django_tz

from bills.models import Attachment, Bill, BillAttachment
from bills.models import CurrencyInfo
from shared_models.models import CountryInfo, Entity, User, UserEntity, UserToken


@pytest.fixture(autouse=True)
def _block_real_token_service_calls(monkeypatch):
    """Stop the suite reaching a live Flask app over HTTP.

    `resolve_xero_access_token_for_entity` POSTs to the Flask token service whenever the
    stored token is expired or absent. Unblocked, the suite talks to whatever is
    listening on PETTY_CASH_URL — on a developer machine that is a running Minty against
    a real database, and that endpoint can spend a single-use Xero refresh token.

    Tests that exercise the token service patch `requests.post` themselves; those
    patches are applied inside the test and take precedence over this one.
    """

    def _blocked(*args, **kwargs):
        raise requests.ConnectionError("real HTTP blocked in tests")

    monkeypatch.setattr("bills.services.xero_token_service.requests.post", _blocked)


@pytest.fixture(autouse=True)
def _registry_rows(request):
    """The country / currency rows the entity fixtures point at.

    ``entities.country_code`` and ``entities.currency_id`` are real FKs on Postgres; SQLite
    (tables from the models) never enforced them, which is how fixtures got away with
    ``country_code="HK"`` and a made-up currency id for so long. Runs only for tests that
    touch the database.
    """
    if "db" not in request.fixturenames and not request.node.get_closest_marker("django_db"):
        return
    request.getfixturevalue("db")
    hkd, _ = CurrencyInfo.objects.get_or_create(
        id="11111111-1111-1111-1111-111111111111",
        defaults={"currency_code": "HKD", "currency_name": "Hong Kong Dollar", "symbol": "HK$",
                  "decimal_places": 2, "is_active": True},
    )
    CountryInfo.objects.get_or_create(
        country_code="HK",
        defaults={"alpha3_code": "HKG", "country_name_en": "Hong Kong", "currency_id": hkd.id,
                  "is_active": True, "display_order": 1},
    )


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def test_user(db):
    return User.objects.create(
        id="96dc83ad-f6b8-5b7d-aa0a-26f5f76f8197",
        email="test@minty.com",
        password="hashed_pw",
        first_name="Test",
        last_name="User",
        username="testuser",
        system_role="normal",
    )


def give_xero_token(user, access_token="access-token", *, expires_in=1800, obtained_at=None,
                    refresh_token="refresh-token", id_token=None):
    """Store a Xero bundle for ``user`` the way Minty does: one ``user_token`` row.

    Minty always writes the access token together with its expiry pair, so a token with
    unknown expiry never occurs in practice; ``_token_expired`` treats one as expired.
    """
    row, _ = UserToken.objects.get_or_create(
        user=user, defaults={"id": uuid.uuid4()}
    )
    row.access_token = access_token
    row.access_token_expires_in = expires_in
    row.access_token_obtained_at = obtained_at or django_tz.now()
    row.refresh_token = refresh_token
    row.id_token = id_token
    row.save()
    return row


@pytest.fixture
def test_entity(db):
    return Entity.objects.create(
        id="9df620d9-a0f3-5f42-a200-04f17c73b009",
        name="Test Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="disconnected",
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
