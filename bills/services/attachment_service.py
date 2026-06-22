import io
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from bills.models import Attachment, Audit, Bill, BillAttachment, Payment, PaymentAttachment
from bills.services.audit_service import log_audit
from bills.services.file_downsize import downsize_bytes
from core.exceptions import BillValidationError

logger = logging.getLogger("minty-api")

# Accepted MIME types. Kept broad so common files coming from the Next.js
# uploader (images, PDFs, Word, Excel, CSV) all pass validation.
ALLOWED_TYPES = {
    # Images
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/heic",
    "image/heif",
    "image/svg+xml",
    # Documents
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # Spreadsheets
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "text/csv",
    "application/csv",
}

# Extension-based fallback for browsers / OS combinations that upload files
# with an empty or incorrect content_type (e.g. HEIC from iPhones, .xlsm
# spreadsheets from older Excel installs).  Maps lower-case extension → MIME.
ALLOWED_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "heic": "image/heic",
    "heif": "image/heif",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    "csv": "text/csv",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _resolve_content_type(file: UploadedFile) -> str | None:
    """Resolve a usable content-type for an uploaded file.

    Inputs : a Django ``UploadedFile``.
    Outputs: a MIME string from ``ALLOWED_TYPES`` if either the browser-supplied
             content_type or the file extension maps to one, otherwise ``None``.

    Browsers occasionally send empty / generic content types (notably
    application/octet-stream for HEIC, .xlsm, etc.), which would otherwise
    cause valid uploads to be rejected and never reach Xero.
    """
    raw = (file.content_type or "").strip().lower()
    if raw and raw in ALLOWED_TYPES:
        return raw

    ext = os.path.splitext(file.name or "")[1].lstrip(".").lower()
    mapped = ALLOWED_EXTENSIONS.get(ext)
    if mapped:
        return mapped

    return None


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.S3_KEY,
        aws_secret_access_key=settings.S3_SECRET,
        region_name=settings.S3_REGION,
    )


def upload_attachment(
    bill: Bill,
    file: UploadedFile,
    user_id: str,
    attachment_role: str = "other",
) -> BillAttachment:
    resolved_content_type = _resolve_content_type(file)
    if resolved_content_type is None:
        raise BillValidationError(
            f"File type '{file.content_type or 'unknown'}' is not allowed"
        )

    if file.size and file.size > MAX_FILE_SIZE:
        raise BillValidationError("File size exceeds 10 MB limit")

    file.seek(0)
    raw = file.read()
    downsized = downsize_bytes(raw, resolved_content_type)
    if len(downsized) < len(raw):
        logger.info(
            "Downsized %s: %d -> %d bytes", file.name, len(raw), len(downsized),
        )

    ext = os.path.splitext(file.name)[1] if file.name else ""
    stored_name = f"{uuid.uuid4().hex}{ext}"
    s3_key = f"attachments/{bill.entity_id}/{bill.id}/{stored_name}"

    s3 = _get_s3_client()
    try:
        s3.upload_fileobj(
            io.BytesIO(downsized),
            settings.S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": resolved_content_type},
        )
    except ClientError as e:
        logger.error("S3 upload failed: %s", e)
        raise BillValidationError("File upload failed. Please try again.")

    with transaction.atomic():
        attachment = Attachment.objects.create(
            original_name=file.name or "unnamed",
            stored_name=stored_name,
            file_path=s3_key,
            mime_type=resolved_content_type,
            file_size=len(downsized),
            file_extension=ext.lstrip("."),
            storage_provider="s3",
            uploaded_by=user_id,
        )

        bill_attachment = BillAttachment.objects.create(
            bill=bill,
            attachment=attachment,
            attachment_role=attachment_role,
            created_by=user_id,
        )

    log_audit(
        bill, Audit.Action.ATTACHMENT_UPLOADED, user_id,
        f"File '{file.name}' uploaded",
    )
    logger.info(
        "Attachment uploaded bill_id=%s file=%s mime=%s",
        bill.id, file.name, resolved_content_type,
    )
    return bill_attachment


def delete_attachment(bill: Bill, bill_attachment_id: str, user_id: str) -> str:
    """Delete a bill attachment from S3 and the database.

    Returns the xero_attachment_id that was on the deleted row (empty string if none),
    so the caller can remove the file from Xero while the token is still in scope.
    """
    try:
        bill_attachment = bill.bill_attachments.select_related("attachment").get(
            id=bill_attachment_id,
        )
    except BillAttachment.DoesNotExist:
        raise BillValidationError("Attachment not found on this bill")

    attachment = bill_attachment.attachment
    # Capture before the row is deleted.
    xero_attachment_id = bill_attachment.xero_attachment_id or ""

    s3 = _get_s3_client()
    try:
        s3.delete_object(Bucket=settings.S3_BUCKET, Key=attachment.file_path)
    except ClientError as e:
        logger.warning("S3 delete failed (proceeding): %s", e)

    with transaction.atomic():
        bill_attachment.delete()
        if not attachment.bill_attachments.exists():
            attachment.delete()

    log_audit(
        bill, Audit.Action.ATTACHMENT_DELETED, user_id,
        f"Attachment '{attachment.original_name}' removed",
    )
    logger.info(
        "Attachment deleted bill_id=%s attachment_id=%s xero_attachment_id=%s by user=%s",
        bill.id, bill_attachment_id, xero_attachment_id or "none", user_id,
    )
    return xero_attachment_id


def serialize_attachment(attachment: Attachment) -> dict:
    """Return a dict matching AttachmentOut, including a presigned S3 download URL.

    Used by both bill and payment API serializers so the shape stays consistent.
    Falls back to an empty string on any S3 error so callers do not need to handle
    exceptions from URL generation.
    """
    try:
        download_url = generate_presigned_download_url(attachment)
    except Exception:
        download_url = ""
    return {
        "id": str(attachment.id),
        "original_name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "file_extension": attachment.file_extension,
        "storage_provider": attachment.storage_provider,
        "created_at": attachment.created_at,
        "download_url": download_url,
    }


def generate_presigned_download_url(attachment: Attachment, expires_in: int = 900) -> str:
    """Generate a presigned S3 URL for downloading an attachment (default 15 min)."""

    s3 = _get_s3_client()
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": attachment.file_path,
                "ResponseContentDisposition": f'inline; filename="{attachment.original_name}"',
                "ResponseContentType": attachment.mime_type or "application/octet-stream",
            },
            ExpiresIn=expires_in,
        )
        return url
    except ClientError as e:
        logger.error("Presigned URL generation failed: %s", e)
        raise BillValidationError("Could not generate download link.")


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT ATTACHMENTS
# ═══════════════════════════════════════════════════════════════════════════

def upload_payment_attachment(
    payment: Payment,
    file: UploadedFile,
    user_id: str,
    attachment_role: str = "other",
) -> PaymentAttachment:
    allowed_roles = {r.value for r in PaymentAttachment.AttachmentRole}
    if attachment_role not in allowed_roles:
        raise BillValidationError(
            f"Invalid attachment_role '{attachment_role}'. "
            f"Allowed: {', '.join(sorted(allowed_roles))}."
        )

    resolved_content_type = _resolve_content_type(file)
    if resolved_content_type is None:
        raise BillValidationError(
            f"File type '{file.content_type or 'unknown'}' is not allowed"
        )

    if file.size and file.size > MAX_FILE_SIZE:
        raise BillValidationError("File size exceeds 10 MB limit")

    file.seek(0)
    raw = file.read()
    downsized = downsize_bytes(raw, resolved_content_type)
    if len(downsized) < len(raw):
        logger.info(
            "Downsized %s: %d -> %d bytes", file.name, len(raw), len(downsized),
        )

    ext = os.path.splitext(file.name)[1] if file.name else ""
    stored_name = f"{uuid.uuid4().hex}{ext}"
    s3_key = f"attachments/payments/{payment.bill_id}/{payment.id}/{stored_name}"

    s3 = _get_s3_client()
    try:
        s3.upload_fileobj(
            io.BytesIO(downsized),
            settings.S3_BUCKET,
            s3_key,
            ExtraArgs={"ContentType": resolved_content_type},
        )
    except ClientError as e:
        logger.error("S3 upload failed: %s", e)
        raise BillValidationError("File upload failed. Please try again.")

    with transaction.atomic():
        attachment = Attachment.objects.create(
            original_name=file.name or "unnamed",
            stored_name=stored_name,
            file_path=s3_key,
            mime_type=resolved_content_type,
            file_size=len(downsized),
            file_extension=ext.lstrip("."),
            storage_provider="s3",
            uploaded_by=user_id,
        )

        payment_attachment = PaymentAttachment.objects.create(
            payment=payment,
            attachment=attachment,
            attachment_role=attachment_role,
            created_by=user_id,
        )

    logger.info(
        "Payment attachment uploaded payment_id=%s file=%s mime=%s",
        payment.id, file.name, resolved_content_type,
    )
    return payment_attachment


def delete_payment_attachment(
    payment: Payment, payment_attachment_id: str, user_id: str
):
    try:
        pa = payment.payment_attachments.select_related("attachment").get(
            id=payment_attachment_id,
        )
    except PaymentAttachment.DoesNotExist:
        raise BillValidationError("Attachment not found on this payment")

    attachment = pa.attachment

    s3 = _get_s3_client()
    try:
        s3.delete_object(Bucket=settings.S3_BUCKET, Key=attachment.file_path)
    except ClientError as e:
        logger.warning("S3 delete failed (proceeding): %s", e)

    with transaction.atomic():
        pa.delete()
        if not attachment.payment_attachments.exists():
            attachment.delete()

    logger.info(
        "Payment attachment deleted payment_id=%s attachment_id=%s by user=%s",
        payment.id, payment_attachment_id, user_id,
    )
