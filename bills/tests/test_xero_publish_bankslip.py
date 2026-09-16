"""
Tests for upload_bankslip_to_xero — verifies the delete-then-upload full-replace
behaviour that prevents duplicate bank-slip files from accumulating on the Xero
invoice across multiple (re)publishes.

Coverage matrix
───────────────
TC-BS-001  First call uploads exactly one file, no DELETE on a clean invoice.
TC-BS-002  Second call (same payment, same attachment) deletes the prior FileId
           tracked locally on the PaymentAttachment, then re-uploads once.
TC-BS-003  Three repeated calls leave exactly one bank-slip file on the invoice
           (regression for the user-reported "stacking" bug).
TC-BS-004  Deletion is filename-scoped: bill attachments uploaded earlier in the
           same publish cycle (named ``*_PAYMENTREQUEST*``) are NEVER deleted by
           the bank-slip step.
TC-BS-005  When local tracking is missing but Xero returns a stale ``*_BANKSLIP*``
           association, the orphan is still cleaned up.
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bills.models import Attachment, Bill, Payment, PaymentAttachment, XeroBillSync
from bills.services.xero_publish_service import (
    _is_bankslip_filename,
    upload_bankslip_to_xero,
)
from shared_models.models import Entity, User, UserEntity

# ── Constants & helpers ─────────────────────────────────────────────────────

FAKE_INVOICE_ID = "xero-invoice-bs-0001"
FAKE_INVOICE_NUMBER = "INV-BS-0001"
FAKE_ORG_ID = "xero-org-bs-001"
FAKE_ACCESS_TOKEN = "fake-bearer-token-bs"
FAKE_USER_ID = "f471f257-f5fb-5ab8-82c2-f336ff8e9b21"  # was "bs-user-001"; user.id is a uuid now
FAKE_S3_BYTES = b"%PDF-1.4 fake bankslip bytes"


_DELETE_PATH = "bills.services.xero_publish_service._delete_xero_file"
_GET_FILES_PATH = "bills.services.xero_publish_service._get_xero_files_for_invoice"
_UPLOAD_ASSOC_PATH = (
    "bills.services.xero_publish_service._upload_and_associate_bill_attachment"
)
_S3_CLIENT_PATH = "bills.services.xero_publish_service._get_s3_client"
_S3_DOWNLOAD_PATH = "bills.services.xero_publish_service._download_from_s3"


@pytest.fixture
def entity(db) -> Entity:
    return Entity.objects.create(
        id="bs-entity-001",
        name="Bankslip Entity",
        country_code="HK",
        currency_id="11111111-1111-1111-1111-111111111111",
        status="connected",
        xero_org_id=FAKE_ORG_ID,
    )


@pytest.fixture
def user(db) -> User:
    return User.objects.create(
        id=FAKE_USER_ID,
        email="bs@minty.com",
        password="hashed",
        first_name="Bs",
        last_name="User",
        username="bsuser",
        system_role="normal",
    )


@pytest.fixture
def user_entity(db, user, entity) -> UserEntity:
    return UserEntity.objects.create(user=user, entity=entity, role="admin")


@pytest.fixture
def published_bill(db, entity, user) -> Bill:
    bill = Bill.objects.create(
        entity_id=entity.id,
        contact="Vendor Co",
        xero_contact_id="xero-contact-bs",
        status=Bill.Status.AUTHORISED,
        amount=Decimal("100.00"),
        description="Bank slip test",
        reference="REF-BS-001",
        invoice_date=datetime.date(2026, 4, 1),
        due_date=datetime.date(2026, 4, 30),
        currency_code="HKD",
        xero_account_code="200",
        published=Bill.PublishStatus.PUBLISHED,
        uploaded_by=user.id,
    )
    XeroBillSync.objects.create(
        bill=bill,
        sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
        sync_type=XeroBillSync.SyncType.CREATE_INVOICE,
        sync_status=XeroBillSync.SyncStatus.SUCCESS,
        request_type="ACCPAY",
        request_status="AUTHORISED",
        request_contact_id=bill.xero_contact_id,
        request_invoice_number=bill.reference,
        request_reference=bill.description,
        response_invoice_id=FAKE_INVOICE_ID,
        response_invoice_number=FAKE_INVOICE_NUMBER,
        idempotency_key="prior-key-bs",
        requested_by=FAKE_USER_ID,
        requested_at=datetime.datetime(
            2026, 4, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
        ),
    )
    return bill


@pytest.fixture
def payment_with_bankslip(
    db, published_bill, user
) -> tuple[Payment, PaymentAttachment]:
    payment = Payment.objects.create(
        bill=published_bill,
        amount=Decimal("100.00"),
        currency_code="HKD",
        payment_method="bank_transfer",
        payment_status=Payment.PaymentStatus.COMPLETED,
        created_by=user.id,
    )
    attachment = Attachment.objects.create(
        original_name="bankslip.pdf",
        stored_name="bankslip-stored.pdf",
        file_path="entity/bs-entity-001/payments/bankslip.pdf",
        mime_type="application/pdf",
        file_size=len(FAKE_S3_BYTES),
        file_extension="pdf",
        storage_provider="s3",
        uploaded_by=user.id,
    )
    pa = PaymentAttachment.objects.create(
        payment=payment,
        attachment=attachment,
        attachment_role=PaymentAttachment.AttachmentRole.BANK_SLIP,
        sort_order=0,
        created_by=user.id,
    )
    return payment, pa


# ─── _is_bankslip_filename unit tests ────────────────────────────────────────


def test_is_bankslip_filename_matches_uppercase_tag():
    assert _is_bankslip_filename("INV-0042_BANKSLIP.pdf") is True


def test_is_bankslip_filename_matches_lowercase_and_indexed():
    assert _is_bankslip_filename("inv-0042_bankslip_2.png") is True


def test_is_bankslip_filename_rejects_payment_request_tag():
    """Bill attachments use the _PAYMENTREQUEST tag and must NOT match."""
    assert _is_bankslip_filename("INV-0042_PAYMENTREQUEST.pdf") is False


def test_is_bankslip_filename_rejects_arbitrary_name():
    assert _is_bankslip_filename("random-invoice.pdf") is False


def test_is_bankslip_filename_handles_empty_input():
    assert _is_bankslip_filename("") is False
    assert _is_bankslip_filename(None) is False  # type: ignore[arg-type]


# ─── upload_bankslip_to_xero behaviour tests ─────────────────────────────────


@pytest.mark.django_db
class TestBankslipUploadIdempotent:
    """upload_bankslip_to_xero must produce exactly one bank-slip file per
    PaymentAttachment, no matter how many times it is called."""

    def test_first_call_uploads_one_file_and_does_not_delete(
        self, payment_with_bankslip, entity, user_entity
    ):
        """TC-BS-001"""
        payment, pa = payment_with_bankslip

        with (
            patch(_GET_FILES_PATH, return_value=[]) as mock_get,
            patch(_DELETE_PATH) as mock_delete,
            patch(_UPLOAD_ASSOC_PATH, return_value="xero-file-001") as mock_upload,
            patch(_S3_CLIENT_PATH, return_value=MagicMock()),
            patch(_S3_DOWNLOAD_PATH, return_value=FAKE_S3_BYTES),
        ):
            result = upload_bankslip_to_xero(
                bill_id=str(payment.bill_id),
                payment_id=str(payment.id),
                entity_id=entity.id,
                user_id=FAKE_USER_ID,
                access_token=FAKE_ACCESS_TOKEN,
            )

        assert result["uploaded"] == 1
        mock_get.assert_called_once()
        mock_delete.assert_not_called()
        mock_upload.assert_called_once()

        pa.refresh_from_db()
        assert pa.xero_attachment_id == "xero-file-001"
        assert "_BANKSLIP" in pa.xero_filename

    def test_second_call_deletes_prior_then_uploads_once(
        self, payment_with_bankslip, entity, user_entity
    ):
        """TC-BS-002 — republish path with locally tracked FileId."""
        payment, pa = payment_with_bankslip
        pa.xero_attachment_id = "old-file-id-001"
        pa.xero_filename = f"{FAKE_INVOICE_NUMBER}_BANKSLIP.pdf"
        pa.save(update_fields=["xero_attachment_id", "xero_filename"])

        with (
            patch(_GET_FILES_PATH, return_value=[]),
            patch(_DELETE_PATH) as mock_delete,
            patch(_UPLOAD_ASSOC_PATH, return_value="new-file-id-001") as mock_upload,
            patch(_S3_CLIENT_PATH, return_value=MagicMock()),
            patch(_S3_DOWNLOAD_PATH, return_value=FAKE_S3_BYTES),
        ):
            upload_bankslip_to_xero(
                bill_id=str(payment.bill_id),
                payment_id=str(payment.id),
                entity_id=entity.id,
                user_id=FAKE_USER_ID,
                access_token=FAKE_ACCESS_TOKEN,
            )

        mock_delete.assert_called_once_with(
            FAKE_ACCESS_TOKEN, FAKE_ORG_ID, "old-file-id-001"
        )
        mock_upload.assert_called_once()

        pa.refresh_from_db()
        assert pa.xero_attachment_id == "new-file-id-001"

    def test_three_calls_leave_exactly_one_file(
        self, payment_with_bankslip, entity, user_entity
    ):
        """TC-BS-003 — regression for the user-reported "3 publishes → 3 copies".

        After three repeated calls the upload helper has been invoked 3 times
        in total (once per call) and the delete helper has been invoked twice
        (once per republish, cleaning up the previous cycle's file)."""
        payment, pa = payment_with_bankslip

        upload_calls: list[str] = []

        def _fake_upload(token, org, invoice_id, filename, file_bytes, content_type):
            new_id = f"file-{len(upload_calls)+1:03d}"
            upload_calls.append(new_id)
            return new_id

        with (
            patch(_GET_FILES_PATH, return_value=[]),
            patch(_DELETE_PATH) as mock_delete,
            patch(_UPLOAD_ASSOC_PATH, side_effect=_fake_upload),
            patch(_S3_CLIENT_PATH, return_value=MagicMock()),
            patch(_S3_DOWNLOAD_PATH, return_value=FAKE_S3_BYTES),
        ):
            for _ in range(3):
                upload_bankslip_to_xero(
                    bill_id=str(payment.bill_id),
                    payment_id=str(payment.id),
                    entity_id=entity.id,
                    user_id=FAKE_USER_ID,
                    access_token=FAKE_ACCESS_TOKEN,
                )

        assert len(upload_calls) == 3, "one upload per call"
        # Two deletes: republish #2 cleans up file-001, republish #3 cleans up file-002.
        assert mock_delete.call_count == 2
        deleted_ids = [c.args[2] for c in mock_delete.call_args_list]
        assert deleted_ids == ["file-001", "file-002"]

        pa.refresh_from_db()
        assert pa.xero_attachment_id == "file-003"

    def test_does_not_delete_bill_attachments_in_same_invoice(
        self, payment_with_bankslip, entity, user_entity
    ):
        """TC-BS-004 — filename filter excludes ``*_PAYMENTREQUEST*`` files
        from the deletion sweep so bill attachments survive the bank-slip step."""
        payment, pa = payment_with_bankslip

        files_on_invoice = [
            {
                "FileId": "bill-att-file-id",
                "Name": f"{FAKE_INVOICE_NUMBER}_PAYMENTREQUEST.pdf",
            },
            {
                "FileId": "stale-bs-file-id",
                "Name": f"{FAKE_INVOICE_NUMBER}_BANKSLIP.pdf",
            },
        ]

        with (
            patch(_GET_FILES_PATH, return_value=files_on_invoice),
            patch(_DELETE_PATH) as mock_delete,
            patch(_UPLOAD_ASSOC_PATH, return_value="new-bs-file"),
            patch(_S3_CLIENT_PATH, return_value=MagicMock()),
            patch(_S3_DOWNLOAD_PATH, return_value=FAKE_S3_BYTES),
        ):
            upload_bankslip_to_xero(
                bill_id=str(payment.bill_id),
                payment_id=str(payment.id),
                entity_id=entity.id,
                user_id=FAKE_USER_ID,
                access_token=FAKE_ACCESS_TOKEN,
            )

        deleted_ids = {c.args[2] for c in mock_delete.call_args_list}
        assert (
            "bill-att-file-id" not in deleted_ids
        ), "bill attachment must be preserved"
        assert "stale-bs-file-id" in deleted_ids, "orphaned bank slip must be deleted"

    def test_orphan_bankslip_without_local_tracking_is_cleaned(
        self, payment_with_bankslip, entity, user_entity
    ):
        """TC-BS-005 — when the PaymentAttachment row was never tagged locally
        (e.g. older row from before this fix), the Xero-side associations
        query is the only signal and the orphan must still be deleted."""
        payment, pa = payment_with_bankslip
        assert pa.xero_attachment_id == ""  # baseline: no local tracking

        files_on_invoice = [
            {"FileId": "orphan-bs-file", "Name": f"{FAKE_INVOICE_NUMBER}_BANKSLIP.pdf"},
        ]

        with (
            patch(_GET_FILES_PATH, return_value=files_on_invoice),
            patch(_DELETE_PATH) as mock_delete,
            patch(_UPLOAD_ASSOC_PATH, return_value="fresh-bs-file"),
            patch(_S3_CLIENT_PATH, return_value=MagicMock()),
            patch(_S3_DOWNLOAD_PATH, return_value=FAKE_S3_BYTES),
        ):
            upload_bankslip_to_xero(
                bill_id=str(payment.bill_id),
                payment_id=str(payment.id),
                entity_id=entity.id,
                user_id=FAKE_USER_ID,
                access_token=FAKE_ACCESS_TOKEN,
            )

        mock_delete.assert_called_once_with(
            FAKE_ACCESS_TOKEN, FAKE_ORG_ID, "orphan-bs-file"
        )
