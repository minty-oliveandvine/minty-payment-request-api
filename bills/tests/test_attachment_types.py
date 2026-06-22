"""
Coverage for the expanded attachment-type whitelist and the helpers that
make Xero accept those uploads.

We assert:
  - The new image / Excel / CSV MIME types pass validation.
  - Files with a missing or octet-stream content_type are still accepted when
    their extension is on the allow-list (e.g. iPhone HEIC, .xlsm).
  - True junk content types (.exe, etc.) still get rejected with 422.
  - The Xero helpers normalise filenames + content-types so the Files API
    does not reject otherwise-valid uploads.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from bills.services.attachment_service import (
    ALLOWED_EXTENSIONS,
    ALLOWED_TYPES,
    _resolve_content_type,
)
from bills.services.xero_publish_service import (
    _resolve_xero_content_type,
    _sanitize_xero_filename,
)


# ─── _resolve_content_type ──────────────────────────────────────────────


class TestResolveContentType:
    def test_browser_sent_jpeg_passes_through(self):
        f = SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")
        assert _resolve_content_type(f) == "image/jpeg"

    def test_browser_sent_xlsx_passes_through(self):
        f = SimpleUploadedFile(
            "report.xlsx",
            b"x",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        assert _resolve_content_type(f) == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_octet_stream_heic_falls_back_to_extension(self):
        # iPhones / Safari sometimes upload HEIC as application/octet-stream.
        f = SimpleUploadedFile(
            "photo.HEIC", b"x", content_type="application/octet-stream",
        )
        assert _resolve_content_type(f) == "image/heic"

    def test_empty_content_type_falls_back_to_extension(self):
        f = SimpleUploadedFile("scan.pdf", b"x", content_type="")
        assert _resolve_content_type(f) == "application/pdf"

    def test_csv_accepted_via_extension(self):
        f = SimpleUploadedFile(
            "rows.csv", b"a,b\n1,2\n", content_type="application/octet-stream",
        )
        assert _resolve_content_type(f) == "text/csv"

    def test_unknown_extension_and_type_rejected(self):
        f = SimpleUploadedFile(
            "malware.exe", b"x", content_type="application/x-msdownload",
        )
        assert _resolve_content_type(f) is None

    def test_every_listed_extension_resolves_to_an_allowed_type(self):
        for ext, mime in ALLOWED_EXTENSIONS.items():
            assert mime in ALLOWED_TYPES, (
                f"extension '{ext}' maps to '{mime}' which is not in ALLOWED_TYPES"
            )


# ─── End-to-end: upload via API ────────────────────────────────────────


@pytest.mark.django_db
class TestExpandedUploadTypes:
    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_xlsx_succeeds(
        self, mock_s3, api_client, auth_headers, draft_bill, test_user_entity,
    ):
        client = MagicMock()
        client.generate_presigned_url.return_value = "https://example/download"
        mock_s3.return_value = client
        f = SimpleUploadedFile(
            "books.xlsx",
            b"PK\x03\x04 fake xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": f},
            **auth_headers,
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body[0]["attachment"]["original_name"] == "books.xlsx"
        assert body[0]["attachment"]["mime_type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    @patch("bills.services.attachment_service._get_s3_client")
    def test_upload_heic_with_octet_stream_succeeds(
        self, mock_s3, api_client, auth_headers, draft_bill, test_user_entity,
    ):
        client = MagicMock()
        client.generate_presigned_url.return_value = "https://example/download"
        mock_s3.return_value = client
        f = SimpleUploadedFile(
            "iphone.HEIC",
            b"\x00\x00\x00\x18ftypheic",
            content_type="application/octet-stream",
        )
        resp = api_client.post(
            f"/api/v1/bills/{draft_bill.id}/attachments",
            data={"files": f},
            **auth_headers,
        )
        assert resp.status_code == 201, resp.content
        # The stored mime_type must be the resolved one, not octet-stream,
        # otherwise Xero will reject the file later.
        assert resp.json()[0]["attachment"]["mime_type"] == "image/heic"


# ─── Xero helpers ──────────────────────────────────────────────────────


class TestXeroFilenameSanitiser:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("INV001_PAYMENTREQUEST.pdf", "INV001_PAYMENTREQUEST.pdf"),
            ("INV001 / new.pdf", "INV001___new.pdf"),
            ('bad<>:"|?*chars.png', "bad_______chars.png"),
            ("  spaced  out.jpg", "spaced_out.jpg"),
            ("", "attachment"),
        ],
    )
    def test_sanitises_forbidden_characters(self, raw, expected):
        assert _sanitize_xero_filename(raw) == expected


class TestResolveXeroContentType:
    def test_keeps_valid_mime_type(self):
        assert _resolve_xero_content_type("image/png", "png") == "image/png"

    def test_replaces_octet_stream_with_extension_mime(self):
        assert (
            _resolve_xero_content_type("application/octet-stream", "pdf")
            == "application/pdf"
        )

    def test_replaces_empty_mime_type_with_extension_mime(self):
        assert _resolve_xero_content_type("", "jpg") == "image/jpeg"

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert (
            _resolve_xero_content_type("", "weirdext")
            == "application/octet-stream"
        )
