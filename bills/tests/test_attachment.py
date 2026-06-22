from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


@pytest.mark.django_db
class TestAttachmentUpload:
    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_pdf(self, mock_s3, api_client, auth_headers, draft_bill, test_user_entity):
        mock_s3.return_value = MagicMock()
        file = SimpleUploadedFile(
            "invoice.pdf",
            b"fake-pdf-content",
            content_type="application/pdf",
        )
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": file},
            **auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body) == 1
        assert body[0]["attachment"]["original_name"] == "invoice.pdf"
        assert body[0]["attachment"]["mime_type"] == "application/pdf"
        assert body[0]["attachment_role"] == "other"

    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_image(self, mock_s3, api_client, auth_headers, draft_bill, test_user_entity):
        mock_s3.return_value = MagicMock()
        file = SimpleUploadedFile(
            "photo.jpg",
            b"fake-image-content",
            content_type="image/jpeg",
        )
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": file},
            **auth_headers,
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 1

    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_multiple_files(self, mock_s3, api_client, auth_headers, draft_bill, test_user_entity):
        """Uploading two files in one request creates two BillAttachment records."""
        mock_s3.return_value = MagicMock()
        pdf = SimpleUploadedFile("invoice.pdf", b"pdf-data", content_type="application/pdf")
        jpg = SimpleUploadedFile("photo.jpg", b"jpg-data", content_type="image/jpeg")
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": [pdf, jpg]},
            **auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body) == 2
        names = {item["attachment"]["original_name"] for item in body}
        assert names == {"invoice.pdf", "photo.jpg"}

    def test_list_attachments_empty(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        resp = api_client.get(
            f"/api/v1/bills/{draft_bill.id}/attachments", **auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_attachments_with_one(
        self, api_client, auth_headers, draft_bill_with_attachment, test_user_entity
    ):
        resp = api_client.get(
            f"/api/v1/bills/{draft_bill_with_attachment.id}/attachments",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["attachment"]["original_name"] == "invoice.pdf"

    @patch("bills.services.attachment_service._get_s3_client")
    def test_delete_attachment(
        self, mock_s3, api_client, auth_headers, draft_bill_with_attachment, test_user_entity
    ):
        mock_s3.return_value = MagicMock()
        ba_id = str(draft_bill_with_attachment.bill_attachments.first().id)
        resp = api_client.delete(
            f"/api/v1/bills/{draft_bill_with_attachment.id}/attachments/{ba_id}",
            **auth_headers,
        )
        assert resp.status_code == 200

    def test_upload_rejected_type(
        self, api_client, auth_headers, draft_bill, test_user_entity
    ):
        file = SimpleUploadedFile(
            "malware.exe",
            b"bad-content",
            content_type="application/x-msdownload",
        )
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": file},
            **auth_headers,
        )
        assert resp.status_code == 422
