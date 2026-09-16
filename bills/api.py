import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests as _requests
from botocore.exceptions import ClientError
from django.conf import settings
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404, StreamingHttpResponse
from ninja import File, Query, Router
from ninja.files import UploadedFile

from bills.models import Bill, Payment, XeroBillSync
from bills.schemas import (
    BillAttachmentOut,
    BillCreateIn,
    BillDraftIn,
    BillFilterQuery,
    BillListOut,
    BillOut,
    BillUpdateIn,
    ErrorOut,
    MessageOut,
    ReturnBillIn,
    SuggestedReferenceOut,
)
from bills.services.attachment_service import (
    _get_s3_client,
    delete_attachment,
    generate_presigned_download_url,
    serialize_attachment,
    upload_attachment,
)
from bills.services.bill_reference_generator import generate_unique_bill_reference
from bills.services.bill_service import (
    create_bill,
    delete_bill,
    save_bill_draft,
    submit_bill,
    update_bill,
    update_bill_draft,
)
from bills.services.payment_service import get_amount_due
from bills.services.xero_publish_service import _delete_xero_file, publish_bill_to_xero
from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.exceptions import BillValidationError
from core.permissions import (
    check_bill_mutable,
    check_create_bill,
    check_delete_bill,
    check_edit_bill,
    check_mark_paid,
    check_not_system_superuser,
    check_publish_xero,
    check_return_bill,
)
from shared_models.models import Entity

logger = logging.getLogger("minty-api")

_HK_TZ = ZoneInfo("Asia/Hong_Kong")
_MS_DATE_RE = re.compile(r"/Date\((-?\d+)[+-]\d+\)/")


def _parse_xero_ms_date(raw):
    """Parse a Xero /Date(ms+offset)/ string to a date in Asia/Hong_Kong."""
    if not raw:
        return None
    m = _MS_DATE_RE.search(str(raw))
    if not m:
        return None
    ms = int(m.group(1))
    dt_utc = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt_utc.astimezone(_HK_TZ).date()


def _backfill_lock_dates(entity_id: str, jwt_user_id: str) -> None:
    """Fetch Xero lock dates synchronously and persist to the Entity row if missing.

    Uses resolve_xero_access_token_for_entity (the same public function used by
    publish_bill_endpoint) so token resolution and refresh are handled consistently.
    Errors are logged at ERROR level and swallowed — the bills list response is
    unaffected regardless of outcome.
    """
    try:
        entity = Entity.objects.filter(id=entity_id).first()
        if not entity or not entity.xero_org_id:
            return
        # Re-check inside the call (guard against race on concurrent requests)
        if (
            entity.period_lock_date is not None
            and entity.end_of_year_lock_date is not None
        ):
            return

        access_token = resolve_xero_access_token_for_entity(entity_id, jwt_user_id)
        if not access_token:
            logger.error(
                "lock date backfill: no access token resolved entity=%s user=%s",
                entity_id,
                jwt_user_id,
            )
            return

        xero_org_id = str(entity.xero_org_id)
        url = "https://api.xero.com/api.xro/2.0/Organisation"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Xero-Tenant-Id": xero_org_id,
            "Accept": "application/json",
        }
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.error(
                "lock date backfill: Xero API returned %s for entity=%s body=%s",
                resp.status_code,
                entity_id,
                (resp.text or "")[:500],
            )
            return

        orgs = resp.json().get("Organisations", [])
        org = next(
            (o for o in orgs if o.get("OrganisationID") == xero_org_id),
            orgs[0] if orgs else None,
        )
        if not org:
            logger.error(
                "lock date backfill: no org in Xero response entity=%s", entity_id
            )
            return

        period_lock_date = _parse_xero_ms_date(org.get("PeriodLockDate"))
        end_of_year_lock_date = _parse_xero_ms_date(org.get("EndOfYearLockDate"))

        Entity.objects.filter(id=entity_id).update(
            period_lock_date=period_lock_date,
            end_of_year_lock_date=end_of_year_lock_date,
        )
        logger.info(
            "lock date backfill: saved entity=%s period=%s eoy=%s",
            entity_id,
            period_lock_date,
            end_of_year_lock_date,
        )
    except Exception as exc:
        logger.error(
            "lock date backfill: failed entity=%s %s: %s",
            entity_id,
            type(exc).__name__,
            exc,
        )


def _get_bill_or_404(bill_id: str, entity_id: str) -> Bill:
    try:
        return Bill.objects.get(id=bill_id, entity_id=entity_id)
    except Bill.DoesNotExist:
        raise Http404("Bill not found")


def _bill_to_out(bill: Bill) -> dict:
    attachments = [
        {
            "id": str(ba.id),
            "attachment": serialize_attachment(ba.attachment),
            "attachment_role": ba.attachment_role,
            "sort_order": ba.sort_order,
            "note": ba.note,
            "created_at": ba.created_at,
        }
        for ba in bill.bill_attachments.select_related("attachment").all()
    ]

    line_items = [
        {
            "id": str(li.id),
            "description": li.description,
            "quantity": li.quantity,
            "unit_amount": li.unit_amount,
            "line_amount": li.line_amount,
            "account_code": li.account_code,
            "account_name": li.account_name,
            "tax_type": li.tax_type,
            "sort_order": li.sort_order,
            "note": li.note,
            "created_at": li.created_at,
            "updated_at": li.updated_at,
        }
        for li in bill.line_items.all()
    ]

    return {
        "id": str(bill.id),
        "entity_id": bill.entity_id,
        "contact": bill.contact,
        "xero_contact_id": bill.xero_contact_id,
        "status": bill.status,
        "amount": bill.amount,
        "amount_due": get_amount_due(bill),
        "description": bill.description,
        "due_date": bill.due_date,
        "invoice_date": bill.invoice_date,
        "reference": bill.reference,
        "currency_code": bill.currency_code,
        "xero_account_code": bill.xero_account_code,
        "published": bill.published,
        "uploaded_by": bill.uploaded_by,
        "created_at": bill.created_at,
        "updated_at": bill.updated_at,
        "attachments": attachments,
        "line_items": line_items,
    }


# ═══════════════════════════════════════════════════════════════════════════
# BILLS
# ═══════════════════════════════════════════════════════════════════════════

bills_router = Router()


@bills_router.post(
    "/", response={201: BillOut, 422: ErrorOut}, summary="Create a new bill"
)
def create_bill_endpoint(request, payload: BillCreateIn):
    check_not_system_superuser(request, "create bills")
    check_create_bill(request.entity_role)
    bill = create_bill(payload, str(request.auth_user.id), request.entity_id)
    return 201, _bill_to_out(bill)


@bills_router.post(
    "/submit/",
    response={201: BillOut, 422: ErrorOut},
    summary="Create and submit a bill (with validation)",
)
def submit_bill_endpoint(request, payload: BillCreateIn):
    check_not_system_superuser(request, "submit bills")
    check_create_bill(request.entity_role)
    bill = submit_bill(payload, str(request.auth_user.id), request.entity_id)
    return 201, _bill_to_out(bill)


@bills_router.post(
    "/draft/",
    response={201: BillOut, 422: ErrorOut},
    summary="Save a new bill as draft (no validation)",
)
def save_draft_endpoint(request, payload: BillDraftIn):
    check_not_system_superuser(request, "save drafts")
    check_create_bill(request.entity_role)
    bill = save_bill_draft(payload, str(request.auth_user.id), request.entity_id)
    return 201, _bill_to_out(bill)


@bills_router.put(
    "/{bill_id}/draft",
    response={200: BillOut, 404: ErrorOut, 422: ErrorOut},
    summary="Update an existing bill as draft (no validation)",
)
def update_draft_endpoint(request, bill_id: str, payload: BillDraftIn):
    check_not_system_superuser(request, "update drafts")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    # Voided bills are fully immutable for all roles; check this first.
    if bill.status == "voided":
        check_bill_mutable(bill.status)
    # Role check runs before the paid-immutability guard so elevated roles
    # (Accountant, Admin, Super Admin) can edit paid bills as per the spec.
    check_edit_bill(request.entity_role, bill.status)
    bill = update_bill_draft(bill, payload, str(request.auth_user.id))
    return _bill_to_out(bill)


@bills_router.get("/", response=list[BillListOut], summary="List bills")
def list_bills(request, filters: Query[BillFilterQuery]):
    from bills.services.flask_billing_sync import (
        trigger_chart_sync_if_changed,
        trigger_flask_contact_sync,
    )

    trigger_chart_sync_if_changed(request, request.entity_id)
    trigger_flask_contact_sync(request, request.entity_id)

    _entity = Entity.objects.filter(id=request.entity_id).first()
    if (
        _entity
        and _entity.xero_org_id
        and (_entity.period_lock_date is None or _entity.end_of_year_lock_date is None)
    ):
        _backfill_lock_dates(request.entity_id, str(request.auth_user.id))

    completed_payment_date = Subquery(
        Payment.objects.filter(
            bill=OuterRef("pk"),
            payment_status=Payment.PaymentStatus.COMPLETED,
        )
        .order_by("-created_at")
        .values("payment_date")[:1]
    )
    qs = Bill.objects.filter(entity_id=request.entity_id).annotate(
        paid_at=completed_payment_date
    )

    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.contact:
        qs = qs.filter(contact__icontains=filters.contact)
    if filters.search and filters.search.strip():
        q = filters.search.strip()
        qs = qs.filter(Q(contact__icontains=q) | Q(description__icontains=q))
    if filters.amount_min is not None:
        qs = qs.filter(amount__gte=filters.amount_min)
    if filters.amount_max is not None:
        qs = qs.filter(amount__lte=filters.amount_max)
    ALLOWED_DATE_FIELDS = {"invoice_date", "created_at"}
    df = (
        filters.date_field
        if filters.date_field in ALLOWED_DATE_FIELDS
        else "created_at"
    )
    if filters.date_from:
        qs = qs.filter(**{f"{df}__gte": filters.date_from})
    if filters.date_to:
        qs = qs.filter(**{f"{df}__lte": filters.date_to})

    allowed_sorts = {
        "created_at",
        "-created_at",
        "amount",
        "-amount",
        "due_date",
        "-due_date",
        "contact",
        "-contact",
        "status",
        "-status",
    }
    sort = filters.sort_by if filters.sort_by in allowed_sorts else "-created_at"
    qs = qs.order_by(sort)

    page_size = min(max(1, filters.page_size), 100)
    page = max(1, filters.page)
    offset = (page - 1) * page_size

    bills = list(qs[offset : offset + page_size])
    return [
        {
            "id": str(b.id),
            "entity_id": b.entity_id,
            "contact": b.contact,
            "status": b.status,
            "amount": b.amount,
            "amount_due": get_amount_due(b),
            "description": b.description,
            "due_date": b.due_date,
            "invoice_date": b.invoice_date,
            "reference": b.reference,
            "currency_code": b.currency_code,
            "xero_account_code": b.xero_account_code,
            "published": b.published,
            "created_at": b.created_at,
            "uploaded_by": b.uploaded_by,
            "paid_at": b.paid_at,
        }
        for b in bills
    ]


@bills_router.get(
    "/suggested-reference/",
    response={200: SuggestedReferenceOut},
    summary="Suggested bill number (MBI + 3 name letters + HK time/date, unique in entity)",
)
def suggested_bill_reference_endpoint(request):
    check_create_bill(request.entity_role)
    ref = generate_unique_bill_reference(request.entity_id, request.auth_user)
    return {"reference": ref}


@bills_router.get(
    "/{bill_id}",
    response={200: BillOut, 404: ErrorOut},
    summary="Get bill detail",
)
def get_bill(request, bill_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    return _bill_to_out(bill)


@bills_router.put(
    "/{bill_id}",
    response={200: BillOut, 404: ErrorOut, 422: ErrorOut},
    summary="Update a bill (response includes payment-synced status and amount_due—merge into UI state or refetch GET detail)",
)
def update_bill_endpoint(request, bill_id: str, payload: BillUpdateIn):
    check_not_system_superuser(request, "update bills")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    # Voided bills are fully immutable for all roles; check this first.
    if bill.status == "voided":
        check_bill_mutable(bill.status)
    # Role check runs before the paid-immutability guard so elevated roles
    # (Accountant, Admin, Super Admin) can edit paid bills as per the spec.
    check_edit_bill(request.entity_role, bill.status)
    if payload.status == "paid" and bill.status != "paid":
        check_mark_paid(request.entity_role)
    bill = update_bill(bill, payload, str(request.auth_user.id))
    return _bill_to_out(bill)


@bills_router.delete(
    "/{bill_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete draft bill or void non-draft bill",
)
def delete_bill_endpoint(request, bill_id: str):
    check_not_system_superuser(request, "delete bills")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    check_delete_bill(request.entity_role, bill.status)
    outcome = delete_bill(bill, str(request.auth_user.id))
    message = "Bill deleted" if outcome == "deleted" else "Bill voided"
    return {"message": message}


@bills_router.post(
    "/{bill_id}/return/",
    response={200: BillOut, 422: ErrorOut},
    summary="Return, un-return, or void a payment request",
)
def return_bill(request, bill_id: str, payload: ReturnBillIn):
    """
    Handles three status transitions based on payload.status:

    - "payment_requested": bill must be in 'submitted' status → sets to 'returned'
    - "returned":          bill must be in 'returned' status  → sets back to 'submitted'
    - "void":              bill must be in 'returned' status  → sets to 'voided'

    Requires Accountant, Admin, or Super Admin role for all transitions.
    """
    check_not_system_superuser(request, "return bills")
    check_return_bill(request.entity_role)

    bill = _get_bill_or_404(bill_id, request.entity_id)

    action = (payload.status or "").strip().lower()

    if action == "void":
        if bill.status != Bill.Status.RETURNED:
            return 422, {
                "detail": "Only bills in 'Returned' status can be voided via this action."
            }
        bill.status = Bill.Status.VOIDED

    elif action == "payment_requested":
        if bill.status != Bill.Status.SUBMITTED:
            return 422, {
                "detail": (
                    "This action only applies to bills in 'Payment Requested' status. "
                    f"Current status: '{bill.status}'."
                )
            }
        bill.status = Bill.Status.RETURNED

    elif action == "returned":
        if bill.status != Bill.Status.RETURNED:
            return 422, {
                "detail": (
                    "This action only applies to bills in 'Returned' status. "
                    f"Current status: '{bill.status}'."
                )
            }
        bill.status = Bill.Status.SUBMITTED

    else:
        return 422, {
            "detail": (
                "Invalid status value. Accepted values: "
                "'payment_requested', 'returned', 'void'."
            )
        }

    bill.save(update_fields=["status", "updated_at"])
    logger.info(
        "return_bill: bill_id=%s action=%s new_status=%s user=%s",
        bill_id,
        action,
        bill.status,
        str(request.auth_user.id),
    )
    return _bill_to_out(bill)


@bills_router.post(
    "/{bill_id}/publish/",
    response={200: BillOut, 404: ErrorOut, 422: ErrorOut},
    summary="Publish a bill to Xero",
)
def publish_bill_endpoint(request, bill_id: str):
    """Build the Xero ACCPAY invoice, send it, upload attachments, and log everything."""
    check_not_system_superuser(request, "publish bills")
    check_publish_xero(request.entity_role)
    _get_bill_or_404(bill_id, request.entity_id)

    access_token = resolve_xero_access_token_for_entity(
        request.entity_id,
        str(request.auth_user.id),
    )
    result = publish_bill_to_xero(
        bill_id=bill_id,
        entity_id=request.entity_id,
        user_id=str(request.auth_user.id),
        access_token=access_token or "",
    )

    # Do not take the service's word for it. Every failure path in
    # publish_bill_to_xero raises today, but the endpoint returning 200 is
    # decided here, so confirm against the sync row that was actually written.
    sync_id = result.get("sync_id")
    if sync_id:
        sync = XeroBillSync.objects.filter(id=sync_id).first()
        if sync and sync.sync_status != XeroBillSync.SyncStatus.SUCCESS:
            raise BillValidationError(
                sync.error_message
                or "Xero did not confirm this publish. Please try again."
            )

    return _bill_to_out(result["bill"])


# ═══════════════════════════════════════════════════════════════════════════
# ATTACHMENTS
# ═══════════════════════════════════════════════════════════════════════════

attachments_router = Router()


def _bill_attachment_to_out(bill_attachment) -> dict:
    """Serialize a BillAttachment to the BillAttachmentOut dict shape."""
    att = bill_attachment.attachment
    return {
        "id": str(bill_attachment.id),
        "attachment": serialize_attachment(att),
        "attachment_role": bill_attachment.attachment_role,
        "sort_order": bill_attachment.sort_order,
        "note": bill_attachment.note,
        "created_at": bill_attachment.created_at,
    }


@attachments_router.post(
    "/{bill_id}/attachments",
    response={201: list[BillAttachmentOut], 422: ErrorOut},
    summary="Upload one or more attachments to a bill",
)
def upload_attachment_endpoint(
    request, bill_id: str, files: list[UploadedFile] = File(...)
):
    """Accept one or multiple files in a single multipart/form-data request.

    The frontend should send the files under the ``files`` field name.
    Each file is validated, stored in S3, and linked to the bill.
    Returns the list of created BillAttachment objects.
    """
    check_not_system_superuser(request, "upload attachments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    if bill.status == "voided":
        check_bill_mutable(bill.status)
    check_edit_bill(request.entity_role, bill.status)

    created = []
    for file in files:
        bill_attachment = upload_attachment(bill, file, str(request.auth_user.id))
        created.append(_bill_attachment_to_out(bill_attachment))

    return 201, created


@attachments_router.get(
    "/{bill_id}/attachments",
    response=list[BillAttachmentOut],
    summary="List attachments for a bill",
)
def list_attachments(request, bill_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    return [
        _bill_attachment_to_out(ba)
        for ba in bill.bill_attachments.select_related("attachment").all()
    ]


@attachments_router.delete(
    "/{bill_id}/attachments/{attachment_id}",
    response={200: MessageOut, 404: ErrorOut},
    summary="Delete an attachment",
)
def delete_attachment_endpoint(request, bill_id: str, attachment_id: str):
    check_not_system_superuser(request, "delete attachments")
    bill = _get_bill_or_404(bill_id, request.entity_id)
    if bill.status == "voided":
        check_bill_mutable(bill.status)
    check_edit_bill(request.entity_role, bill.status)

    xero_attachment_id = delete_attachment(bill, attachment_id, str(request.auth_user.id))

    # If the bill is published and the attachment had a Xero file, delete it from
    # Xero immediately so it does not reappear on the next republish.
    if xero_attachment_id and bill.published == Bill.PublishStatus.PUBLISHED:
        try:
            entity = Entity.objects.get(id=bill.entity_id)
            access_token = resolve_xero_access_token_for_entity(
                str(request.entity_id),
                str(request.auth_user.id),
            )
            _delete_xero_file(access_token, entity.xero_org_id, xero_attachment_id)
        except Exception as exc:
            logger.warning(
                "Xero file delete on attachment removal failed "
                "bill=%s xero_attachment_id=%s: %s",
                bill_id,
                xero_attachment_id,
                exc,
            )

    return {"message": "Attachment deleted"}


@attachments_router.get(
    "/{bill_id}/attachments/{attachment_id}/download",
    response={200: dict, 404: ErrorOut},
    summary="Get a presigned download URL for a bill attachment",
)
def download_bill_attachment(request, bill_id: str, attachment_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    try:
        ba = bill.bill_attachments.select_related("attachment").get(id=attachment_id)
    except Exception:
        from django.http import Http404

        raise Http404("Attachment not found")
    att = ba.attachment
    url = generate_presigned_download_url(att)
    return {
        "url": url,
        "original_name": att.original_name,
        "mime_type": att.mime_type,
        "file_size": att.file_size,
    }


@attachments_router.get(
    "/{bill_id}/attachments/{attachment_id}/preview/",
    summary="Proxy-stream a bill attachment from B2 storage (avoids cross-origin iframe blocks)",
)
def preview_bill_attachment(request, bill_id: str, attachment_id: str):
    """Fetch the attachment from B2 server-to-server and stream it to the browser.

    Uses a direct boto3 get_object call (not a presigned URL) so the browser
    always loads the file from the trusted Django API origin, bypassing
    browser policies that block cross-origin iframes to storage URLs.

    Content-Disposition is set to 'inline' so the browser renders the file
    (e.g. PDF plugin) rather than triggering a download.
    """
    bill = _get_bill_or_404(bill_id, request.entity_id)
    try:
        ba = bill.bill_attachments.select_related("attachment").get(id=attachment_id)
    except Exception:
        raise Http404("Attachment not found")

    att = ba.attachment
    s3 = _get_s3_client()
    try:
        s3_response = s3.get_object(Bucket=settings.S3_BUCKET, Key=att.file_path)
    except ClientError as e:
        logger.error(
            "S3 get_object failed for preview bill=%s att=%s: %s",
            bill_id,
            attachment_id,
            e,
        )
        raise Http404("File not found in storage")

    content_type = att.mime_type or s3_response.get(
        "ContentType", "application/octet-stream"
    )
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
    # Allow the browser's PDF plugin to operate inside a same-origin iframe.
    streaming_response["X-Frame-Options"] = "SAMEORIGIN"
    return streaming_response
