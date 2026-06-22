import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from bills.models import Attachment, Bill, BillAttachment, BillLineItem
from shared_models.models import Entity, User, UserEntity


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def test_user(db):
    return User.objects.create(
        id="test-user-001",
        email="test@minty.com",
        password="hashed_pw",
        first_name="Test",
        last_name="User",
        username="testuser",
        system_role="user",
    )


@pytest.fixture
def test_entity(db):
    return Entity.objects.create(
        id="test-entity-001",
        name="Test Entity",
        country_code="HK",
        currency_code="HKD",
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
