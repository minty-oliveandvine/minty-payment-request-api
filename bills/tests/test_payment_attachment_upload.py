"""
Demo / regression: payment (e.g. bank slip) file upload hits S3 and persists Attachment + PaymentAttachment.

S3 is mocked; we assert upload_fileobj was called with the expected key and that DB rows match.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from bills.models import Attachment, Payment, PaymentAttachment


@pytest.fixture
def pending_payment(db, draft_bill, test_user):
    """Payment on draft bill — eligible for attachment upload."""
    return Payment.objects.create(
        bill=draft_bill,
        payment_date="2026-03-20",
        amount="100.00",
        currency_code="HKD",
        payment_status="pending",
        created_by=test_user.id,
    )


@pytest.mark.django_db
class TestPaymentAttachmentBankSlipUpload:
    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_bank_slip_pdf_calls_s3_and_persists_db(
        self,
        mock_get_s3,
        api_client,
        auth_headers,
        draft_bill,
        pending_payment,
        test_user_entity,
    ):
        mock_s3 = MagicMock()
        mock_get_s3.return_value = mock_s3

        pdf = SimpleUploadedFile(
            "bank-slip-demo.pdf",
            b"%PDF-1.4 demo bank slip bytes",
            content_type="application/pdf",
        )

        url = (
            f"/api/v1/bills/{draft_bill.id}/payments/{pending_payment.id}/attachments"
            "?attachment_role=bank_slip"
        )
        resp = api_client.post(url, data={"file": pdf}, **auth_headers)

        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["attachment"]["original_name"] == "bank-slip-demo.pdf"
        assert body["attachment"]["mime_type"] == "application/pdf"
        assert body["attachment"]["storage_provider"] == "s3"
        assert body["attachment_role"] == "bank_slip"

        mock_s3.upload_fileobj.assert_called_once()
        call_args = mock_s3.upload_fileobj.call_args
        file_arg, bucket, key = call_args[0]
        assert bucket == settings.S3_BUCKET
        assert key.startswith(
            f"attachments/payments/{draft_bill.id}/{pending_payment.id}/"
        )
        extra = call_args.kwargs.get("ExtraArgs") or {}
        assert extra.get("ContentType") == "application/pdf"

        att = Attachment.objects.get(id=body["attachment"]["id"])
        assert att.file_path == key
        assert att.original_name == "bank-slip-demo.pdf"
        assert att.storage_provider == "s3"

        pa = PaymentAttachment.objects.get(id=body["id"])
        assert str(pa.payment_id) == str(pending_payment.id)
        assert str(pa.attachment_id) == str(att.id)
        assert pa.attachment_role == PaymentAttachment.AttachmentRole.BANK_SLIP

    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_default_role_other_without_query(
        self,
        mock_get_s3,
        api_client,
        auth_headers,
        draft_bill,
        pending_payment,
        test_user_entity,
    ):
        mock_get_s3.return_value = MagicMock()
        pdf = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/payments/{pending_payment.id}/attachments",
            data={"file": pdf},
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["attachment_role"] == "other"

    def test_upload_rejects_invalid_attachment_role(
        self,
        api_client,
        auth_headers,
        draft_bill,
        pending_payment,
        test_user_entity,
    ):
        pdf = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/payments/{pending_payment.id}/attachments"
            "?attachment_role=not_a_real_role",
            data={"file": pdf},
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "attachment_role" in resp.json().get("detail", "").lower()
