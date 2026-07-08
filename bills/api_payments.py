import logging

from botocore.exceptions import ClientError
from django.conf import settings
from django.http import Http404, StreamingHttpResponse
from ninja import File, Query, Router
from ninja.files import UploadedFile

from django.db.models import Sum

from bills.models import Bill, Payment
from shared_models.models import User
from bills.schemas import (
    ErrorOut,
    MessageOut,
    PaymentAttachmentOut,
    PaymentCreateIn,
    PaymentFilterQuery,
    PaymentListResponse,
    PaymentOut,
    PaymentUpdateIn,
)
from bills.services.attachment_service import (
    _get_s3_client,
    delete_payment_attachment,
    generate_presigned_download_url,
    serialize_attachment,
    upload_payment_attachment,
)
from bills.services.payment_service import create_payment, delete_payment, update_payment
from bills.services.xero_publish_service import upload_bankslip_background
from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.exceptions import BillValidationError, PermissionDeniedError
from core.permissions import check_bill_mutable, check_mark_paid, check_not_system_superuser

logger = logging.getLogger("minty-api")


def _get_bill_or_404(bill_id: str, entity_id: str) -> Bill:
    try:
        return Bill.objects.get(id=bill_id, entity_id=entity_id)
    except Bill.DoesNotExist:
        raise Http404("Bill not found")


def _get_payment_or_404(payment_id: str, bill: Bill) -> Payment:
    try:
        return Payment.objects.get(id=payment_id, bill=bill)
    except Payment.DoesNotExist:
        raise Http404("Payment not found")


def _same_supplier_bill_ids(bill: Bill) -> list[str]:
    """
    Bill IDs in the same entity that share this bill's supplier: xero_contact_id
    when set, otherwise exact contact name. Empty supplier matches only this bill.
    """
    qs = Bill.objects.filter(entity_id=bill.entity_id)
    xero_id = (bill.xero_contact_id or "").strip()
    if xero_id:
        qs = qs.filter(xero_contact_id=xero_id)
    else:
        contact = (bill.contact or "").strip()
        if not contact:
            return [str(bill.id)]
        qs = qs.filter(contact=bill.contact)
    return [str(pk) for pk in qs.values_list("id", flat=True)]


def _resolve_user_name(user_id: str, users: dict) -> str:
    user = users.get(user_id)
    if not user:
        return ""
    first = user.first_name or ""
    last = user.last_name or ""
    return f"{first} {last}".strip()


def _payment_to_list_out(payment: Payment, users: dict) -> dict:
    b = payment.bill
    return {
        "id": str(payment.id),
        "bill_id": str(payment.bill_id),
        "bill_reference": b.reference or "",
        "bill_status": b.status or "",
        "payment_date": payment.payment_date,
        "amount": payment.amount,
        "payment_method": payment.payment_method,
        "payment_status": payment.payment_status,
        "reference_no": payment.reference_no,
        "created_by": payment.created_by,
        "created_by_name": _resolve_user_name(payment.created_by, users),
        "created_at": payment.created_at,
    }


def _payment_to_out(payment: Payment) -> dict:
    attachments = [
        {
            "id": str(pa.id),
            "attachment": serialize_attachment(pa.attachment),
            "attachment_role": pa.attachment_role,
            "sort_order": pa.sort_order,
            "note": pa.note,
            "created_at": pa.created_at,
        }
        for pa in payment.payment_attachments.select_related("attachment").all()
    ]

    return {
        "id": str(payment.id),
        "bill_id": str(payment.bill_id),
        "payment_date": payment.payment_date,
        "amount": payment.amount,
        "currency_code": payment.currency_code,
        "payment_method": payment.payment_method,
        "payment_status": payment.payment_status,
        "reference_no": payment.reference_no,
        "note": payment.note,
        "xero_payment_id": payment.xero_payment_id,
        "created_by": payment.created_by,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
        "attachments": attachments,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════

payments_router = Router()


@payments_router.post(
    "/{bill_id}/payments",
    response={201: PaymentOut, 404: ErrorOut},
    summary="Create a payment for a bill",
)
def create_payment_endpoint(request, bill_id: str, payload: PaymentCreateIn):
    check_not_system_superuser(request, "create payments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    check_bill_mutable(bill.status)
    check_mark_paid(request.entity_role)
    payment = create_payment(bill, payload, request.auth_user.id)
    return 201, _payment_to_out(payment)


@payments_router.get(
    "/{bill_id}/payments",
    response={200: PaymentListResponse, 404: ErrorOut},
    summary="List payments for supplier (all bills with same contact / xero_contact_id)",
)
def list_payments(request, bill_id: str, filters: Query[PaymentFilterQuery]):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    supplier_bill_ids = _same_supplier_bill_ids(bill)
    logger.info(
        "list_payments bill_id=%s supplier_bill_count=%s",
        bill_id,
        len(supplier_bill_ids),
    )
    qs = Payment.objects.filter(bill_id__in=supplier_bill_ids).select_related("bill")

    if filters.payment_status:
        qs = qs.filter(payment_status=filters.payment_status)
    if filters.date_from:
        qs = qs.filter(payment_date__gte=filters.date_from)
    if filters.date_to:
        qs = qs.filter(payment_date__lte=filters.date_to)

    allowed_sorts = {
        "created_at", "-created_at",
        "amount", "-amount",
        "payment_date", "-payment_date",
    }
    sort = filters.sort_by if filters.sort_by in allowed_sorts else "-payment_date"
    secondary = "-created_at" if sort.startswith("-") else "created_at"
    qs = qs.order_by(sort, secondary)

    page_size = min(max(1, filters.page_size), 100)
    page = max(1, filters.page)
    offset = (page - 1) * page_size

    paid_total = (
        Payment.objects.filter(bill=bill, payment_status="completed")
        .aggregate(total=Sum("amount"))["total"]
    ) or 0

    page_qs = list(qs[offset : offset + page_size])
    user_ids = {p.created_by for p in page_qs if p.created_by}
    users = {str(u.id): u for u in User.objects.filter(id__in=user_ids)}

    return {
        "paid_total": paid_total,
        "payments": [_payment_to_list_out(p, users) for p in page_qs],
    }


@payments_router.get(
    "/{bill_id}/payments/{payment_id}",
    response={200: PaymentOut, 404: ErrorOut},
    summary="Get payment detail",
)
def get_payment(request, bill_id: str, payment_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    payment = _get_payment_or_404(payment_id, bill)
    return _payment_to_out(payment)


@payments_router.put(
    "/{bill_id}/payments/{payment_id}",
    response={200: PaymentOut, 404: ErrorOut},
    summary="Update a payment",
)
def update_payment_endpoint(
    request, bill_id: str, payment_id: str, payload: PaymentUpdateIn
):
    check_not_system_superuser(request, "update payments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    check_bill_mutable(bill.status)
    check_mark_paid(request.entity_role)
    payment = _get_payment_or_404(payment_id, bill)
    payment = update_payment(payment, payload, request.auth_user.id)
    return _payment_to_out(payment)


@payments_router.delete(
    "/{bill_id}/payments/{payment_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete a payment",
)
def delete_payment_endpoint(request, bill_id: str, payment_id: str):
    check_not_system_superuser(request, "delete payments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    check_mark_paid(request.entity_role)
    payment = _get_payment_or_404(payment_id, bill)
    delete_payment(payment, request.auth_user.id)
    return {"message": "Payment deleted"}


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT ATTACHMENTS
# ═══════════════════════════════════════════════════════════════════════════

payment_attachments_router = Router()


@payment_attachments_router.post(
    "/{bill_id}/payments/{payment_id}/attachments",
    response={201: PaymentAttachmentOut, 404: ErrorOut, 422: ErrorOut},
    summary="Upload attachment to a payment",
)
def upload_payment_attachment_endpoint(
    request,
    bill_id: str,
    payment_id: str,
    file: UploadedFile = File(...),
    attachment_role: str = Query("other"),
):
    check_not_system_superuser(request, "upload payment attachments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    if bill.status == "voided":
        raise PermissionDeniedError("Cannot modify payments on a voided bill.")
    check_mark_paid(request.entity_role)
    payment = _get_payment_or_404(payment_id, bill)
    pa = upload_payment_attachment(
        payment, file, request.auth_user.id, attachment_role=attachment_role
    )

    if bill.published == Bill.PublishStatus.PUBLISHED:
        try:
            access_token = resolve_xero_access_token_for_entity(
                request.entity_id, request.auth_user.id,
            ) or ""
        except BillValidationError as exc:
            logger.warning("Skipping background bankslip upload — token unavailable: %s", exc)
            access_token = ""
        if access_token:
            upload_bankslip_background(
                bill_id=bill_id,
                payment_id=payment_id,
                entity_id=request.entity_id,
                user_id=str(request.auth_user.id),
                access_token=access_token,
            )

    return 201, {
        "id": str(pa.id),
        "attachment": serialize_attachment(pa.attachment),
        "attachment_role": pa.attachment_role,
        "sort_order": pa.sort_order,
        "note": pa.note,
        "created_at": pa.created_at,
    }


@payment_attachments_router.get(
    "/{bill_id}/payments/{payment_id}/attachments",
    response={200: list[PaymentAttachmentOut], 404: ErrorOut},
    summary="List attachments for a payment",
)
def list_payment_attachments(request, bill_id: str, payment_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    payment = _get_payment_or_404(payment_id, bill)
    return [
        {
            "id": str(pa.id),
            "attachment": serialize_attachment(pa.attachment),
            "attachment_role": pa.attachment_role,
            "sort_order": pa.sort_order,
            "note": pa.note,
            "created_at": pa.created_at,
        }
        for pa in payment.payment_attachments.select_related("attachment").all()
    ]


@payment_attachments_router.delete(
    "/{bill_id}/payments/{payment_id}/attachments/{attachment_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete a payment attachment",
)
def delete_payment_attachment_endpoint(
    request, bill_id: str, payment_id: str, attachment_id: str
):
    check_not_system_superuser(request, "delete payment attachments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    if bill.status == "voided":
        raise PermissionDeniedError("Cannot modify payments on a voided bill.")
    check_mark_paid(request.entity_role)
    payment = _get_payment_or_404(payment_id, bill)
    delete_payment_attachment(payment, attachment_id, request.auth_user.id)
    return {"message": "Payment attachment deleted"}


@payment_attachments_router.get(
    "/{bill_id}/payments/{payment_id}/attachments/{attachment_id}/download",
    response={200: dict, 404: ErrorOut},
    summary="Get a presigned download URL for a payment attachment",
)
def download_payment_attachment(
    request, bill_id: str, payment_id: str, attachment_id: str
):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    payment = _get_payment_or_404(payment_id, bill)
    try:
        pa = payment.payment_attachments.select_related("attachment").get(
            id=attachment_id,
        )
    except Exception:
        raise Http404("Payment attachment not found")
    att = pa.attachment
    url = generate_presigned_download_url(att)
    return {
        "url": url,
        "original_name": att.original_name,
        "mime_type": att.mime_type,
        "file_size": att.file_size,
    }


@payment_attachments_router.get(
    "/{bill_id}/payments/{payment_id}/attachments/{attachment_id}/preview/",
    summary="Proxy-stream a payment attachment from B2 storage (avoids cross-origin iframe blocks)",
)
def preview_payment_attachment(
    request, bill_id: str, payment_id: str, attachment_id: str
):
    """Fetch the payment attachment from B2 server-to-server and stream it to the browser.

    Uses a direct boto3 get_object call (not a presigned URL) so the browser
    always loads the file from the trusted Django API origin, bypassing
    browser policies that block cross-origin requests to storage URLs.

    Content-Disposition is set to 'inline' so the browser renders the file
    (e.g. PDF plugin) rather than triggering a download.
    """
    bill = _get_bill_or_404(bill_id, request.entity_id)
    payment = _get_payment_or_404(payment_id, bill)
    try:
        pa = payment.payment_attachments.select_related("attachment").get(
            id=attachment_id,
        )
    except Exception:
        raise Http404("Payment attachment not found")

    att = pa.attachment
    s3 = _get_s3_client()
    try:
        s3_response = s3.get_object(Bucket=settings.S3_BUCKET, Key=att.file_path)
    except ClientError as e:
        logger.error(
            "S3 get_object failed for preview bill=%s payment=%s att=%s: %s",
            bill_id,
            payment_id,
            attachment_id,
            e,
        )
        raise Http404("File not found in storage")

    content_type = att.mime_type or s3_response.get("ContentType", "application/octet-stream")
    filename = att.original_name or att.stored_name or "file"

    def _stream(body):
        chunk_size = 64 * 1024  # 64 KB
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    streaming_response = StreamingHttpResponse(
        _stream(s3_response["Body"]),
        content_type=content_type,
    )
    streaming_response["Content-Disposition"] = f'inline; filename="{filename}"'
    content_length = s3_response.get("ContentLength")
    if content_length is not None:
        streaming_response["Content-Length"] = str(content_length)
    # Allow the browser's PDF plugin to operate inside a same-origin context.
    streaming_response["X-Frame-Options"] = "SAMEORIGIN"
    return streaming_response
