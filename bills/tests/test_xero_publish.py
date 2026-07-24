"""
Tests for publish_bill_to_xero and _update_bill_to_xero in xero_publish_service.

Coverage matrix
───────────────
Happy path
  TC-PUB-001  First publish (no prior sync) → PUT /Invoices, CREATE_INVOICE sync type
  TC-PUB-002  Republish (prior successful sync) → POST /Invoices/{id}, UPDATE_INVOICE sync type
  TC-PUB-003  Republish sends updated bill amount / line-item data to Xero

Edge cases
  TC-PUB-010  Prior sync exists but response_invoice_id is empty → treated as first publish (PUT)
  TC-PUB-011  Prior sync exists but status is FAILED → treated as first publish (PUT)
  TC-PUB-012  Multiple prior syncs → most recent successful one is used for the invoice ID

Failure handling
  TC-PUB-020  Republish Xero returns non-200 → bill.published = FAILED, BillValidationError raised
  TC-PUB-021  Republish raises RequestException → bill.published = FAILED, BillValidationError raised
  TC-PUB-022  First publish Xero returns non-200 → bill.published = FAILED, BillValidationError raised
  TC-PUB-023  First publish raises RequestException → bill.published = FAILED, BillValidationError raised

Audit trail
  TC-PUB-030  First publish success → audit detail contains "Published to Xero. Invoice #"
  TC-PUB-031  Republish success → audit detail contains "Updated Xero invoice #"

Guard rails
  TC-PUB-040  Entity has no xero_org_id → BillValidationError before any HTTP call
  TC-PUB-041  access_token is empty → BillValidationError before any HTTP call
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from bills.models import Audit, Bill, BillLineItem, XeroBillSync, XeroBillSyncPayload
from bills.services.xero_publish_service import publish_bill_to_xero
from core.exceptions import BillValidationError
from shared_models.models import Entity, User, UserEntity


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS / SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════════════════

FAKE_INVOICE_ID = "xero-invoice-uuid-0001"
FAKE_INVOICE_NUMBER = "INV-0042"
FAKE_ORG_ID = "xero-org-001"
FAKE_ACCESS_TOKEN = "fake-bearer-token"
FAKE_USER_ID = "pub-user-001"


def _xero_200(invoice_id=FAKE_INVOICE_ID, invoice_number=FAKE_INVOICE_NUMBER) -> MagicMock:
    """Return a mock requests.Response that looks like a successful Xero 200."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json.return_value = {
        "Invoices": [
            {
                "InvoiceID": invoice_id,
                "InvoiceNumber": invoice_number,
                "Status": "AUTHORISED",
                "AmountDue": 500.00,
                "AmountPaid": 0.00,
                "Total": 500.00,
                "CurrencyCode": "HKD",
                "LineItems": [
                    {
                        "LineItemID": "li-xero-001",
                        "Description": "Test service",
                        "Quantity": 1.0,
                        "UnitAmount": 500.00,
                        "LineAmount": 500.00,
                        "TaxType": "NONE",
                        "TaxAmount": 0.00,
                        "AccountCode": "200",
                        "AccountID": "acc-001",
                        "ValidationErrors": [],
                    }
                ],
            }
        ],
        "ProviderName": "Minty Test",
        "DateTimeUTC": "/Date(1712534400000+0000)/",
    }
    return resp


def _xero_400(message="Invalid invoice") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 400
    resp.headers = {"Content-Type": "application/json"}
    resp.text = message
    resp.json.return_value = {"Message": message}
    return resp


def _xero_422_with_validation_errors() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 422
    resp.headers = {"Content-Type": "application/json"}
    resp.text = "Unprocessable"
    resp.json.return_value = {
        "Elements": [
            {"ValidationErrors": [{"Message": "Contact not found in Xero"}]}
        ]
    }
    return resp


@pytest.fixture
def xero_entity(db) -> Entity:
    return Entity.objects.create(
        id="pub-entity-001",
        name="Publish Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="connected",
        xero_org_id=FAKE_ORG_ID,
    )


@pytest.fixture
def xero_user(db) -> User:
    return User.objects.create(
        id=FAKE_USER_ID,
        email="publisher@minty.com",
        password="hashed_pw",
        first_name="Pub",
        last_name="Lisher",
        username="publisher",
        system_role="user",
    )


@pytest.fixture
def xero_user_entity(db, xero_user, xero_entity) -> UserEntity:
    return UserEntity.objects.create(
        user=xero_user,
        entity=xero_entity,
        role="admin",
    )


@pytest.fixture
def authorised_bill(db, xero_entity, xero_user) -> Bill:
    """A ready-to-publish AUTHORISED bill with a Xero contact linked."""
    return Bill.objects.create(
        entity_id=xero_entity.id,
        contact="Acme Corp",
        xero_contact_id="xero-contact-abc",
        status=Bill.Status.AUTHORISED,
        amount=Decimal("500.00"),
        description="Office supplies",
        reference="INV-LOCAL-001",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        xero_account_code="200",
        uploaded_by=xero_user.id,
    )


@pytest.fixture
def bill_with_line_items(db, xero_entity, xero_user) -> Bill:
    """Bill that has explicit line items (non-fallback path in payload builder)."""
    bill = Bill.objects.create(
        entity_id=xero_entity.id,
        contact="Line Corp",
        xero_contact_id="xero-contact-line",
        status=Bill.Status.AUTHORISED,
        amount=Decimal("300.00"),
        description="Consulting",
        reference="INV-LINE-001",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        uploaded_by=xero_user.id,
    )
    BillLineItem.objects.create(
        bill=bill,
        description="Consulting hours",
        quantity=Decimal("3.0000"),
        unit_amount=Decimal("100.00"),
        line_amount=Decimal("300.00"),
        account_code="300",
        tax_type="NONE",
        sort_order=0,
    )
    return bill


def _make_successful_sync(bill: Bill, invoice_id: str = FAKE_INVOICE_ID) -> XeroBillSync:
    """Insert a completed CREATE_INVOICE sync so subsequent calls see a prior success."""
    return XeroBillSync.objects.create(
        bill=bill,
        sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
        sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
        sync_status=XeroBillSync.SyncStatus.SUCCESS,
        request_type="ACCPAY",
        request_status="AUTHORISED",
        request_contact_id=bill.xero_contact_id or "",
        request_invoice_number=bill.reference or "",
        request_reference=bill.description or "",
        response_invoice_id=invoice_id,
        response_invoice_number=FAKE_INVOICE_NUMBER,
        idempotency_key="prior-key-001",
        requested_by=FAKE_USER_ID,
        requested_at=datetime.datetime(2026, 4, 1, 10, 0, 0, tzinfo=datetime.timezone.utc),
    )


# Patch target for the two HTTP verbs used by the service
_PUT_PATH = "bills.services.xero_publish_service.requests.put"
_POST_PATH = "bills.services.xero_publish_service.requests.post"
# Attachment upload helpers touch S3 and re-resolve tokens; silence them globally
_UPLOAD_ATTACHMENTS_PATH = "bills.services.xero_publish_service._upload_bill_attachments_to_xero"
_UPLOAD_BANKSLIPS_PATH = "bills.services.xero_publish_service._upload_existing_bankslips_to_xero"


# ═══════════════════════════════════════════════════════════════════════════
# HAPPY PATH
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestFirstPublish:
    """TC-PUB-001: No prior successful sync → PUT /Invoices, CREATE_INVOICE."""

    def test_uses_put_not_post(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_POST_PATH) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        mock_put.assert_called_once()
        mock_post.assert_not_called()

    def test_put_targets_invoices_endpoint(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        called_url = mock_put.call_args[0][0]
        assert called_url.endswith("/Invoices"), (
            f"Expected PUT to /Invoices, got: {called_url}"
        )

    def test_creates_sync_with_create_invoice_type(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sync = XeroBillSync.objects.get(bill=authorised_bill)
        assert sync.sync_type == XeroBillSync.SyncType.CREATE_INVOICE
        assert sync.sync_status == XeroBillSync.SyncStatus.SUCCESS

    def test_bill_marked_published(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.PUBLISHED

    def test_sync_is_not_flagged_as_republish(self, authorised_bill, xero_entity, xero_user_entity):
        """CREATE_INVOICE syncs must not have is_republish semantics reflected in sync_type."""
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sync = XeroBillSync.objects.get(bill=authorised_bill)
        assert sync.sync_type != XeroBillSync.SyncType.UPDATE_INVOICE

    def test_response_invoice_id_persisted(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200(invoice_id="new-xero-id")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sync = XeroBillSync.objects.get(bill=authorised_bill)
        assert sync.response_invoice_id == "new-xero-id"

    def test_payload_includes_xero_contact_id(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sent_json = mock_put.call_args[1]["json"]
        contact_id = sent_json["Invoices"][0]["Contact"]["ContactID"]
        assert contact_id == authorised_bill.xero_contact_id

    def test_bearer_token_in_request_headers(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        headers = mock_put.call_args[1]["headers"]
        assert headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
        assert headers["Xero-Tenant-Id"] == FAKE_ORG_ID

    def test_payload_stored_in_sync_payload_row(self, authorised_bill, xero_entity, xero_user_entity):
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sync = XeroBillSync.objects.get(bill=authorised_bill)
        assert XeroBillSyncPayload.objects.filter(xero_bill_sync=sync).exists()


@pytest.mark.django_db
class TestRepublish:
    """TC-PUB-002: Prior successful sync exists → POST /Invoices/{InvoiceID}, UPDATE_INVOICE."""

    def test_uses_post_not_put(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        mock_post.assert_called_once()
        mock_put.assert_not_called()

    def test_post_targets_specific_invoice_id(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill, invoice_id=FAKE_INVOICE_ID)

        with (
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        called_url = mock_post.call_args[0][0]
        assert called_url.endswith(f"/Invoices/{FAKE_INVOICE_ID}"), (
            f"Expected POST to /Invoices/{{id}}, got: {called_url}"
        )

    def test_creates_sync_with_update_invoice_type(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        # Two syncs exist: the seed one (CREATE) and the new republish (UPDATE)
        update_sync = XeroBillSync.objects.filter(
            bill=authorised_bill,
            sync_type=XeroBillSync.SyncType.UPDATE_INVOICE,
        ).first()
        assert update_sync is not None
        assert update_sync.sync_status == XeroBillSync.SyncStatus.SUCCESS

    def test_bill_marked_published_after_republish(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.PUBLISHED

    def test_sync_record_count_incremented(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        # Original seed sync + new UPDATE_INVOICE sync
        assert XeroBillSync.objects.filter(bill=authorised_bill).count() == 2


@pytest.mark.django_db
class TestRepublishSendsUpdatedData:
    """TC-PUB-003: Republish sends the current bill data (not stale prior-sync snapshot)."""

    def test_updated_amount_in_payload(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        # Simulate an edit between first publish and republish
        authorised_bill.amount = Decimal("999.00")
        authorised_bill.save()

        with (
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sent_json = mock_post.call_args[1]["json"]
        # No explicit line items on this bill → falls back to header amount
        unit_amount = sent_json["Invoices"][0]["LineItems"][0]["UnitAmount"]
        assert unit_amount == pytest.approx(999.00), (
            "Republish must use current bill.amount, not the original 500"
        )

    def test_updated_line_items_in_payload(self, bill_with_line_items, xero_entity, xero_user_entity):
        _make_successful_sync(bill_with_line_items)

        # Update the existing line item's amount
        li = bill_with_line_items.line_items.first()
        li.unit_amount = Decimal("150.00")
        li.line_amount = Decimal("450.00")
        li.save()

        with (
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(bill_with_line_items.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        sent_json = mock_post.call_args[1]["json"]
        sent_unit_amount = sent_json["Invoices"][0]["LineItems"][0]["UnitAmount"]
        assert sent_unit_amount == pytest.approx(150.00)

    def test_bearer_token_in_republish_headers(self, authorised_bill, xero_entity, xero_user_entity):
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200()) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"
        assert headers["Xero-Tenant-Id"] == FAKE_ORG_ID


# ═══════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestPriorSyncEdgeCases:

    def test_tc_pub_010_empty_invoice_id_treated_as_first_publish(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-010: Sync exists but response_invoice_id is '' → fall through to PUT."""
        XeroBillSync.objects.create(
            bill=authorised_bill,
            sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
            sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
            sync_status=XeroBillSync.SyncStatus.SUCCESS,
            request_type="ACCPAY",
            request_status="AUTHORISED",
            request_contact_id="",
            request_invoice_number="",
            request_reference="",
            response_invoice_id="",          # empty — the guard must catch this
            response_invoice_number="",
            idempotency_key="empty-id-key",
            requested_by=FAKE_USER_ID,
            requested_at=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
        )

        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_POST_PATH) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        mock_put.assert_called_once()
        mock_post.assert_not_called()

    def test_tc_pub_011_failed_sync_treated_as_first_publish(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-011: Only prior sync is FAILED → _get_latest_successful_sync returns None → PUT."""
        XeroBillSync.objects.create(
            bill=authorised_bill,
            sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
            sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
            sync_status=XeroBillSync.SyncStatus.FAILED,   # not SUCCESS
            request_type="ACCPAY",
            request_status="AUTHORISED",
            request_contact_id="",
            request_invoice_number="",
            request_reference="",
            response_invoice_id="",
            response_invoice_number="",
            idempotency_key="failed-key",
            requested_by=FAKE_USER_ID,
            requested_at=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
        )

        with (
            patch(_PUT_PATH, return_value=_xero_200()) as mock_put,
            patch(_POST_PATH) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        mock_put.assert_called_once()
        mock_post.assert_not_called()

    def test_tc_pub_012_uses_most_recent_successful_sync(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-012: Two successful syncs → republish targets the newer invoice ID."""
        older_invoice_id = "older-xero-id"
        newer_invoice_id = "newer-xero-id"

        # Older sync (created first, lower created_at ordering)
        _make_successful_sync(authorised_bill, invoice_id=older_invoice_id)

        # Newer sync — created a second later so ordering puts it first
        XeroBillSync.objects.create(
            bill=authorised_bill,
            sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
            sync_type=XeroBillSync.SyncType.UPDATE_INVOICE,
            sync_status=XeroBillSync.SyncStatus.SUCCESS,
            request_type="ACCPAY",
            request_status="AUTHORISED",
            request_contact_id="",
            request_invoice_number="",
            request_reference="",
            response_invoice_id=newer_invoice_id,
            response_invoice_number="INV-0043",
            idempotency_key="newer-key",
            requested_by=FAKE_USER_ID,
            requested_at=datetime.datetime(2026, 4, 2, tzinfo=datetime.timezone.utc),
        )

        with (
            patch(_POST_PATH, return_value=_xero_200(invoice_id=newer_invoice_id)) as mock_post,
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        called_url = mock_post.call_args[0][0]
        assert newer_invoice_id in called_url, (
            f"Expected POST to URL containing '{newer_invoice_id}', got: {called_url}"
        )
        assert older_invoice_id not in called_url


# ═══════════════════════════════════════════════════════════════════════════
# FAILURE HANDLING
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestRepublishFailures:

    def test_tc_pub_020_non_200_response_sets_bill_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-020: Xero returns 400 on republish → bill.published = FAILED."""
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_400("Contact not found")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.FAILED

    def test_tc_pub_020_non_200_raises_bill_validation_error(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-020: Xero returns non-200 on republish → BillValidationError is raised."""
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_400("Contact not found")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        assert "rejected" in str(exc_info.value).lower()

    def test_tc_pub_020_non_200_sync_status_is_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-020: Failed republish → the new UPDATE_INVOICE sync row is marked FAILED."""
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_422_with_validation_errors()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        update_sync = XeroBillSync.objects.filter(
            bill=authorised_bill,
            sync_type=XeroBillSync.SyncType.UPDATE_INVOICE,
        ).first()
        assert update_sync is not None
        assert update_sync.sync_status == XeroBillSync.SyncStatus.FAILED
        assert update_sync.has_errors is True

    def test_tc_pub_021_request_exception_sets_bill_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-021: requests.post raises RequestException → bill.published = FAILED."""
        import requests as req_lib

        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, side_effect=req_lib.ConnectionError("Network down")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        assert "API call failed" in str(exc_info.value)
        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.FAILED

    def test_tc_pub_021_request_exception_sync_marked_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-021: Network error on republish → UPDATE_INVOICE sync row is FAILED."""
        import requests as req_lib

        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, side_effect=req_lib.Timeout("Timed out")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        update_sync = XeroBillSync.objects.filter(
            bill=authorised_bill,
            sync_type=XeroBillSync.SyncType.UPDATE_INVOICE,
        ).first()
        assert update_sync is not None
        assert update_sync.sync_status == XeroBillSync.SyncStatus.FAILED
        assert "Timed out" in update_sync.error_message


@pytest.mark.django_db
class TestFirstPublishFailures:

    def test_tc_pub_022_non_200_sets_bill_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-022: Xero returns 400 on first publish → bill.published = FAILED."""
        with (
            patch(_PUT_PATH, return_value=_xero_400("Missing contact")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.FAILED

    def test_tc_pub_022_non_200_sync_status_is_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-022: Failed first publish → the CREATE_INVOICE sync row is FAILED."""
        with (
            patch(_PUT_PATH, return_value=_xero_400()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError):
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        sync = XeroBillSync.objects.get(
            bill=authorised_bill,
            sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
        )
        assert sync.sync_status == XeroBillSync.SyncStatus.FAILED

    def test_tc_pub_023_request_exception_sets_bill_failed(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-023: requests.put raises RequestException on first publish → FAILED + error raised."""
        import requests as req_lib

        with (
            patch(_PUT_PATH, side_effect=req_lib.ConnectionError("No route to host")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        assert "API call failed" in str(exc_info.value)
        authorised_bill.refresh_from_db()
        assert authorised_bill.published == Bill.PublishStatus.FAILED


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestAuditTrail:

    def test_tc_pub_030_first_publish_audit_message(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-030: First publish success → audit detail starts with 'Published to Xero. Invoice #'."""
        with (
            patch(_PUT_PATH, return_value=_xero_200(invoice_number="INV-0042")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        audit = Audit.objects.filter(
            bill=authorised_bill,
            action=Audit.Action.PUBLISHED_TO_XERO,
        ).last()
        assert audit is not None
        assert audit.detail.startswith("Published to Xero. Invoice #")
        assert "INV-0042" in audit.detail

    def test_tc_pub_031_republish_audit_message(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-031: Republish success → audit detail starts with 'Updated Xero invoice #'."""
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200(invoice_number="INV-0042")),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        # The republish audit is the most recent PUBLISHED_TO_XERO entry
        audit = Audit.objects.filter(
            bill=authorised_bill,
            action=Audit.Action.PUBLISHED_TO_XERO,
        ).last()
        assert audit is not None
        assert audit.detail.startswith("Updated Xero invoice #")
        assert "INV-0042" in audit.detail

    def test_first_publish_audit_user_id_recorded(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """Audit row must carry the requesting user_id."""
        with (
            patch(_PUT_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        audit = Audit.objects.filter(
            bill=authorised_bill,
            action=Audit.Action.PUBLISHED_TO_XERO,
        ).last()
        assert audit.user_id == FAKE_USER_ID

    def test_republish_audit_user_id_recorded(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        _make_successful_sync(authorised_bill)

        with (
            patch(_POST_PATH, return_value=_xero_200()),
            patch(_UPLOAD_ATTACHMENTS_PATH),
            patch(_UPLOAD_BANKSLIPS_PATH),
        ):
            publish_bill_to_xero(
                str(authorised_bill.id), xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        audit = Audit.objects.filter(
            bill=authorised_bill,
            action=Audit.Action.PUBLISHED_TO_XERO,
        ).last()
        assert audit.user_id == FAKE_USER_ID


# ═══════════════════════════════════════════════════════════════════════════
# GUARD RAILS — pre-HTTP validation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestGuardRails:

    def test_tc_pub_040_no_xero_org_id_raises(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-040: Entity.xero_org_id is blank → BillValidationError, no HTTP call made."""
        xero_entity.xero_org_id = ""
        xero_entity.save()

        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH) as mock_post,
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID, FAKE_ACCESS_TOKEN,
                )

        assert "no xero organization" in str(exc_info.value).lower()
        mock_put.assert_not_called()
        mock_post.assert_not_called()

    def test_tc_pub_041_empty_access_token_raises(
        self, authorised_bill, xero_entity, xero_user_entity,
    ):
        """TC-PUB-041: access_token is '' → BillValidationError, no HTTP call made."""
        with (
            patch(_PUT_PATH) as mock_put,
            patch(_POST_PATH) as mock_post,
        ):
            with pytest.raises(BillValidationError) as exc_info:
                publish_bill_to_xero(
                    str(authorised_bill.id), xero_entity.id,
                    FAKE_USER_ID,
                    "",  # empty token
                )

        assert "reconnect to xero" in str(exc_info.value).lower()
        mock_put.assert_not_called()
        mock_post.assert_not_called()

    def test_bill_not_found_raises(self, xero_entity, xero_user_entity):
        """Passing a non-existent bill_id raises BillValidationError."""
        with pytest.raises(BillValidationError) as exc_info:
            publish_bill_to_xero(
                "non-existent-bill-id", xero_entity.id,
                FAKE_USER_ID, FAKE_ACCESS_TOKEN,
            )

        assert "bill not found" in str(exc_info.value).lower()


# ═══════════════════════════════════════════════════════════════════════════
# ATTACHMENT UPLOAD — _upload_bill_attachments_to_xero unit tests
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.django_db
class TestUploadBillAttachmentsToXero:
    """
    Direct unit tests for _upload_bill_attachments_to_xero.

    Uses the Xero Files API (files.xro/1.0) for upload, association, and deletion.

    Full-replace semantics: every publish/republish queries Xero directly for
    current file associations, deletes them all, then re-uploads everything the
    bill currently has in Minty.

    TC-ATT-001  Single attachment → uploaded at slot 0 → "{inv}_PAYMENTREQUEST.pdf"
    TC-ATT-002  Two attachments on republish → old Xero file deleted first,
                both re-uploaded at slots 0 and 1
    TC-ATT-003  Three attachments → uploaded at slots 0, 1, 2 with correct filenames
    TC-ATT-004  Existing Xero files are all deleted before upload regardless of count
    TC-ATT-005  xero_attachment_id / xero_filename are reset to "" before re-upload,
                then set to new FileId on success
    TC-ATT-006  Deleted (is_deleted=True) attachment is skipped even in full-replace
    TC-ATT-007  S3 download failure is skipped gracefully (no upload called)
    TC-ATT-008  Upload+associate returns None → xero_attachment_id stays blank
    TC-ATT-009  invoice_id blank → returns immediately without any HTTP call
    TC-ATT-010  Bill has no attachments → Xero files are still cleared, no upload
    """

    def _make_sync(self, bill, invoice_id="xero-inv-001", invoice_number="INV-001"):
        from bills.models import XeroBillSync
        return XeroBillSync(
            bill=bill,
            sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
            sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
            sync_status=XeroBillSync.SyncStatus.SUCCESS,
            request_type="ACCPAY",
            request_status="AUTHORISED",
            request_contact_id="",
            request_invoice_number="",
            request_reference="",
            response_invoice_id=invoice_id,
            response_invoice_number=invoice_number,
            idempotency_key="k",
            requested_by=FAKE_USER_ID,
            requested_at=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
        )

    def _make_attachment(self, bill, user, *, xero_attachment_id="", xero_filename="",
                         is_deleted=False, ext="pdf"):
        """Create an Attachment + BillAttachment pair and return the BillAttachment."""
        from bills.models import Attachment, BillAttachment
        att = Attachment.objects.create(
            original_name=f"file.{ext}",
            stored_name=f"stored.{ext}",
            file_path=f"attachments/{bill.id}/stored.{ext}",
            mime_type="application/pdf",
            file_size=1024,
            file_extension=ext,
            storage_provider="s3",
            uploaded_by=user.id,
            is_deleted=is_deleted,
        )
        return BillAttachment.objects.create(
            bill=bill,
            attachment=att,
            attachment_role="other",
            created_by=user.id,
            xero_attachment_id=xero_attachment_id,
            xero_filename=xero_filename,
        )

    _TOKEN_PATH = "bills.services.xero_publish_service.resolve_xero_access_token_for_entity"
    _S3_PATH = "bills.services.xero_publish_service._get_s3_client"
    _GET_FILES_PATH = "bills.services.xero_publish_service._get_xero_files_for_invoice"
    _UPLOAD_ASSOC_PATH = "bills.services.xero_publish_service._upload_and_associate_bill_attachment"
    _DEL_FILE_PATH = "bills.services.xero_publish_service._delete_xero_file"

    def _s3_mock(self, file_bytes=b"PDFDATA"):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=file_bytes))}
        return s3

    def test_tc_att_001_single_attachment_uploaded_at_slot_zero(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-001: Single attachment → uploaded at slot 0, filename has no suffix."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        ba = self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[]),
            patch(self._UPLOAD_ASSOC_PATH, return_value="xero-file-new-001") as mock_upload,
            patch(self._DEL_FILE_PATH) as mock_del,
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_del.assert_not_called()
        assert mock_upload.call_count == 1
        used_filename = mock_upload.call_args[0][3]
        assert used_filename == "INV-001_PAYMENTREQUEST.pdf"

        ba.refresh_from_db()
        assert ba.xero_filename == "INV-001_PAYMENTREQUEST.pdf"
        assert ba.xero_attachment_id == "xero-file-new-001"

    def test_tc_att_002_republish_both_attachments_uploaded_fresh(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-002: Two attachments on republish — old Xero file deleted first,
        both re-uploaded at slots 0 and 1."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        old_file_id = "xero-file-old-001"
        ba_first = self._make_attachment(
            authorised_bill, xero_user,
            xero_attachment_id=old_file_id,
            xero_filename="INV-001_PAYMENTREQUEST.pdf",
        )
        ba_second = self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        uploaded_filenames = []

        def fake_upload(access_token, xero_org_id, invoice_id, filename, file_bytes, content_type):
            uploaded_filenames.append(filename)
            return f"new-{filename}"

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[
                {"FileId": old_file_id, "Name": "INV-001_PAYMENTREQUEST.pdf"},
            ]),
            patch(self._UPLOAD_ASSOC_PATH, side_effect=fake_upload),
            patch(self._DEL_FILE_PATH) as mock_del,
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_del.assert_called_once()
        assert mock_del.call_args[0][2] == old_file_id  # (access_token, xero_org_id, file_id)

        assert len(uploaded_filenames) == 2
        assert "INV-001_PAYMENTREQUEST.pdf" in uploaded_filenames
        assert "INV-001_PAYMENTREQUEST_1.pdf" in uploaded_filenames

        ba_first.refresh_from_db()
        assert ba_first.xero_filename == "INV-001_PAYMENTREQUEST.pdf"
        assert ba_first.xero_attachment_id == "new-INV-001_PAYMENTREQUEST.pdf"

        ba_second.refresh_from_db()
        assert ba_second.xero_filename == "INV-001_PAYMENTREQUEST_1.pdf"
        assert ba_second.xero_attachment_id == "new-INV-001_PAYMENTREQUEST_1.pdf"

    def test_tc_att_003_three_attachments_get_consecutive_slots(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-003: Three attachments → uploaded at slots 0, 1, 2."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        self._make_attachment(authorised_bill, xero_user)
        self._make_attachment(authorised_bill, xero_user)
        self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        uploaded_filenames = []

        def fake_upload(access_token, xero_org_id, invoice_id, filename, file_bytes, content_type):
            uploaded_filenames.append(filename)
            return f"id-{filename}"

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[]),
            patch(self._UPLOAD_ASSOC_PATH, side_effect=fake_upload),
            patch(self._DEL_FILE_PATH),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        assert len(uploaded_filenames) == 3
        assert "INV-001_PAYMENTREQUEST.pdf" in uploaded_filenames
        assert "INV-001_PAYMENTREQUEST_1.pdf" in uploaded_filenames
        assert "INV-001_PAYMENTREQUEST_2.pdf" in uploaded_filenames

    def test_tc_att_004_multiple_existing_xero_files_all_deleted(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-004: Multiple Xero files are all deleted before upload."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        xero_file_ids = ["file-id-aaa", "file-id-bbb", "file-id-ccc"]
        xero_existing = [
            {"FileId": fid, "Name": f"INV-001_PAYMENTREQUEST_{i}.pdf"}
            for i, fid in enumerate(xero_file_ids)
        ]

        deleted_ids = []

        def fake_del(access_token, xero_org_id, file_id):
            deleted_ids.append(file_id)

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=xero_existing),
            patch(self._UPLOAD_ASSOC_PATH, return_value="new-file-id"),
            patch(self._DEL_FILE_PATH, side_effect=fake_del),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        assert sorted(deleted_ids) == sorted(xero_file_ids)

    def test_tc_att_005_xero_tracking_fields_reset_before_reupload(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-005: xero_attachment_id and xero_filename are cleared, then set to new FileId."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        ba = self._make_attachment(
            authorised_bill, xero_user,
            xero_attachment_id="stale-file-id",
            xero_filename="INV-001_PAYMENTREQUEST.pdf",
        )
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[
                {"FileId": "stale-file-id", "Name": "INV-001_PAYMENTREQUEST.pdf"},
            ]),
            patch(self._UPLOAD_ASSOC_PATH, return_value="fresh-file-id"),
            patch(self._DEL_FILE_PATH),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        ba.refresh_from_db()
        assert ba.xero_attachment_id == "fresh-file-id"
        assert ba.xero_filename == "INV-001_PAYMENTREQUEST.pdf"

    def test_tc_att_006_deleted_attachment_skipped(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-006: is_deleted=True attachment is never uploaded."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        self._make_attachment(authorised_bill, xero_user, is_deleted=True)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[]),
            patch(self._UPLOAD_ASSOC_PATH) as mock_upload,
            patch(self._DEL_FILE_PATH),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_upload.assert_not_called()

    def test_tc_att_007_s3_download_failure_skipped(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-007: S3 download returns None → attachment skipped, no upload called."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        s3_fail = MagicMock()
        from botocore.exceptions import ClientError
        s3_fail.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "get_object",
        )

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=s3_fail),
            patch(self._GET_FILES_PATH, return_value=[]),
            patch(self._UPLOAD_ASSOC_PATH) as mock_upload,
            patch(self._DEL_FILE_PATH),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_upload.assert_not_called()

    def test_tc_att_008_upload_failure_does_not_save(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-008: Upload+associate returns None → xero_attachment_id stays blank."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        ba = self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_number="INV-001")

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[]),
            patch(self._UPLOAD_ASSOC_PATH, return_value=None),
            patch(self._DEL_FILE_PATH),
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        ba.refresh_from_db()
        assert ba.xero_attachment_id == ""
        assert ba.xero_filename == ""

    def test_tc_att_009_blank_invoice_id_returns_immediately(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-009: sync.response_invoice_id is '' → early return, no HTTP calls."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        self._make_attachment(authorised_bill, xero_user)
        sync = self._make_sync(authorised_bill, invoice_id="", invoice_number="INV-001")

        with (
            patch(self._TOKEN_PATH) as mock_token,
            patch(self._UPLOAD_ASSOC_PATH) as mock_upload,
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_token.assert_not_called()
        mock_upload.assert_not_called()

    def test_tc_att_010_no_bill_attachments_xero_is_still_cleared(
        self, authorised_bill, xero_entity, xero_user,
    ):
        """TC-ATT-010: Bill has no attachments — any existing Xero files are still
        deleted (authoritative clear), and no upload is attempted."""
        from bills.services.xero_publish_service import _upload_bill_attachments_to_xero

        sync = self._make_sync(authorised_bill, invoice_number="INV-001")
        orphan_file_id = "aaa-bbb-ccc-111"

        with (
            patch(self._TOKEN_PATH, return_value=FAKE_ACCESS_TOKEN),
            patch(self._S3_PATH, return_value=self._s3_mock()),
            patch(self._GET_FILES_PATH, return_value=[
                {"FileId": orphan_file_id, "Name": "INV-001_PAYMENTREQUEST.pdf"},
            ]),
            patch(self._UPLOAD_ASSOC_PATH) as mock_upload,
            patch(self._DEL_FILE_PATH) as mock_del,
        ):
            _upload_bill_attachments_to_xero(
                authorised_bill, sync, xero_entity.id, FAKE_USER_ID, FAKE_ORG_ID,
            )

        mock_upload.assert_not_called()
        mock_del.assert_called_once()
        deleted_file_id = mock_del.call_args[0][2]  # (access_token, xero_org_id, file_id)
        assert deleted_file_id == orphan_file_id
