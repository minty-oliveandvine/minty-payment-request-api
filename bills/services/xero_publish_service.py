"""
Publish a bill to Xero as an ACCPAY invoice, log sync records, and upload attachments.
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import requests
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import connection, transaction

from bills.models import (
    Audit,
    Bill,
    EntityBillAccountXero,
    Payment,
    PaymentAttachment,
    XeroBillResponseLine,
    XeroBillSync,
    XeroBillSyncLine,
    XeroBillSyncPayload,
)
from bills.services.attachment_service import _get_s3_client
from bills.services.audit_service import log_audit
from bills.services.contact_service import xero_org_scope
from bills.services.xero_token_service import resolve_xero_access_token_for_entity
from core.exceptions import BillValidationError
from shared_models.models import Entity, XeroContactSync

logger = logging.getLogger("minty-api")

XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_FILES_API_BASE = "https://api.xero.com/files.xro/1.0"

# Characters Xero rejects as "Bad Request" when present in a filename.
# Source: developer.xero.com/documentation/api/accounting/attachments
_XERO_FORBIDDEN_FILENAME_CHARS = '<>:"/\\|?*'

# Fallback MIME type per common extension. Used when an Attachment row has
# no mime_type or a generic application/octet-stream — Xero's Files API
# rejects octet-stream uploads for known extensions like .png/.jpg/.pdf.
_EXT_TO_MIME = {
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


def _sanitize_xero_filename(filename: str) -> str:
    """Strip characters Xero rejects from a filename.

    Inputs : the raw filename we want to send to Xero (e.g. ``INV001 / new.pdf``).
    Outputs: a safe filename with forbidden characters replaced by ``_`` and
             collapsed whitespace, e.g. ``INV001___new.pdf``.

    Xero returns 400 Bad Request when the filename contains < > : " / \\ | ? *.
    """
    if not filename:
        return "attachment"
    cleaned = filename
    for ch in _XERO_FORBIDDEN_FILENAME_CHARS:
        cleaned = cleaned.replace(ch, "_")
    cleaned = "_".join(cleaned.split())
    return cleaned or "attachment"


def _resolve_xero_content_type(mime_type: str, ext: str) -> str:
    """Resolve a sensible Content-Type for a Xero file upload.

    Inputs : the stored mime_type on the Attachment row and the file extension.
    Outputs: a non-generic MIME string Xero will accept.

    Xero rejects ``application/octet-stream`` for files with known image /
    document extensions, so we fall back to a per-extension lookup whenever
    the stored mime_type is missing or generic.
    """
    normalised = (mime_type or "").strip().lower()
    if normalised and normalised != "application/octet-stream":
        return normalised
    return _EXT_TO_MIME.get((ext or "").lower(), "application/octet-stream")


def publish_bill_to_xero(bill_id: str, entity_id: str, user_id: str, access_token: str) -> dict:
    """Publish a bill to Xero, log sync data, and upload attachments."""

    logger.info("publish_bill_to_xero: starting bill=%s entity=%s", bill_id, entity_id)

    bill = _load_bill(bill_id, entity_id)
    entity = _load_entity(entity_id)
    xero_org_id = entity.xero_org_id
    if not xero_org_id:
        raise BillValidationError("Entity has no Xero organization linked")
    if not access_token:
        raise BillValidationError("No Xero access token available. Please reconnect to Xero.")

    # Was this bill previously published, and was that to THIS Xero org?
    #
    # This runs before the contact heal below on purpose: when the entity has
    # been reconnected to a different organisation, the cached contact id
    # belongs to the old one and must be dropped first so the heal re-resolves
    # it against the current org.
    prior_sync, foreign_org_sync = _select_successful_sync(str(bill.id), xero_org_id)

    if foreign_org_sync is not None and prior_sync is None:
        _reset_for_new_org(bill, foreign_org_sync, xero_org_id)

    # Heal missing xero_contact_id before building any payload
    if not bill.xero_contact_id:
        sync_row = (
            XeroContactSync.objects.filter(entity_id=bill.entity_id)
            .filter(xero_org_scope(xero_org_id))
            .filter(name__iexact=bill.contact)
            .first()
        )
        if sync_row and sync_row.xero_contact_id:
            bill.xero_contact_id = sync_row.xero_contact_id
            bill.save(update_fields=["xero_contact_id"])
        else:
            raise BillValidationError(
                f"Contact '{bill.contact}' could not be matched to a Xero contact. "
                "Please re-select the contact from the dropdown and save before publishing."
            )

    if prior_sync and prior_sync.response_invoice_id:
        # Republish — update the existing Xero invoice via POST /Invoices/{InvoiceID}
        return _update_bill_to_xero(bill, entity, user_id, access_token, prior_sync)

    # First publish — create a new Xero invoice via PUT /Invoices
    payload = _build_xero_invoice_payload(bill, entity)
    idempotency_key = str(uuid.uuid4())
    sync = _create_sync_record(bill, user_id, idempotency_key, is_republish=False)

    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.put(
            f"{XERO_API_BASE}/Invoices",
            headers=request_headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        _handle_request_exception(sync, bill, user_id, payload, request_headers, exc)
        raise BillValidationError(f"Xero API call failed: {exc}")

    sync = _update_sync_with_response(sync, response, payload, request_headers)

    if sync.sync_status == XeroBillSync.SyncStatus.SUCCESS:
        bill.published = Bill.PublishStatus.PUBLISHED
        bill.save(update_fields=["published", "status", "updated_at"])
        log_audit(
            bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
            f"Published to Xero. Invoice #{sync.response_invoice_number}",
        )
        _upload_bill_attachments_to_xero(bill, sync, entity_id, user_id, xero_org_id)
        _upload_existing_bankslips_to_xero(bill, entity_id, xero_org_id, user_id)
    else:
        bill.published = Bill.PublishStatus.FAILED
        bill.save(update_fields=["published", "updated_at"])
        log_audit(
            bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
            f"Publish failed: {sync.error_message[:200]}",
        )
        raise BillValidationError(f"Xero rejected the invoice: {sync.error_message}")

    logger.info("publish_bill_to_xero: completed bill=%s sync=%s", bill_id, sync.id)
    return {"bill": bill, "sync_id": str(sync.id)}


def _update_bill_to_xero(
    bill: Bill, entity: Entity, user_id: str, access_token: str, prior_sync: XeroBillSync,
) -> dict:
    """Update an existing Xero invoice via POST /Invoices/{InvoiceID}.

    Special case: if the new target status is DRAFT but the prior sync was AUTHORISED,
    Xero will reject a direct status downgrade. Instead we VOID the existing invoice
    and create a brand-new DRAFT invoice, then record the new InvoiceID on the sync row.
    """

    xero_org_id = entity.xero_org_id
    invoice_id = prior_sync.response_invoice_id

    logger.info(
        "_update_bill_to_xero: updating invoice=%s bill=%s", invoice_id, bill.id,
    )

    payload = _build_xero_invoice_payload(bill, entity)
    target_status = payload["Invoices"][0]["Status"]

    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # If the invoice date has moved on or before a lock date the new status will be
    # DRAFT, but the existing Xero invoice is already AUTHORISED. Xero does not allow
    # a direct AUTHORISED → DRAFT transition, so we must VOID the old invoice first
    # and then create a fresh DRAFT invoice.
    if target_status == "DRAFT" and prior_sync.response_status == "AUTHORISED":
        logger.info(
            "_update_bill_to_xero: prior invoice=%s is AUTHORISED but new status is DRAFT "
            "— voiding and recreating bill=%s",
            invoice_id, bill.id,
        )

        # Step 1: VOID the existing AUTHORISED invoice.
        void_payload = {"Invoices": [{"InvoiceID": invoice_id, "Status": "VOIDED"}]}
        try:
            void_response = requests.post(
                f"{XERO_API_BASE}/Invoices/{invoice_id}",
                headers=request_headers,
                json=void_payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise BillValidationError(f"Xero VOID request failed: {exc}")

        if void_response.status_code not in (200, 201):
            error_msg = _extract_xero_error(
                void_response.json() if void_response.text else {},
                void_response.text,
            )
            raise BillValidationError(f"Xero rejected the VOID request: {error_msg}")

        logger.info(
            "_update_bill_to_xero: voided invoice=%s status=%s",
            invoice_id, void_response.status_code,
        )

        # Step 2: Create a brand-new DRAFT invoice (no InvoiceID in the body).
        idempotency_key = str(uuid.uuid4())
        sync = _create_sync_record(bill, user_id, idempotency_key, is_republish=True)

        try:
            response = requests.put(
                f"{XERO_API_BASE}/Invoices",
                headers=request_headers,
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            _handle_request_exception(sync, bill, user_id, payload, request_headers, exc)
            raise BillValidationError(f"Xero API call failed: {exc}")

        sync = _update_sync_with_response(sync, response, payload, request_headers)

        if sync.sync_status == XeroBillSync.SyncStatus.SUCCESS:
            bill.published = Bill.PublishStatus.PUBLISHED
            bill.save(update_fields=["published", "status", "updated_at"])
            log_audit(
                bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
                f"Voided AUTHORISED invoice and recreated as DRAFT #{sync.response_invoice_number}",
            )
            _upload_bill_attachments_to_xero(bill, sync, str(entity.id), user_id, xero_org_id)
            _upload_existing_bankslips_to_xero(bill, str(entity.id), xero_org_id, user_id)
        else:
            bill.published = Bill.PublishStatus.FAILED
            bill.save(update_fields=["published", "updated_at"])
            log_audit(
                bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
                f"Recreate as DRAFT failed: {sync.error_message[:200]}",
            )
            raise BillValidationError(f"Xero rejected the new DRAFT invoice: {sync.error_message}")

        logger.info("_update_bill_to_xero: completed (void+recreate) bill=%s sync=%s", bill.id, sync.id)
        return {"bill": bill, "sync_id": str(sync.id)}

    # Normal update path — status is AUTHORISED, or was already DRAFT in Xero.
    idempotency_key = str(uuid.uuid4())
    sync = _create_sync_record(bill, user_id, idempotency_key, is_republish=True)

    try:
        response = requests.post(
            f"{XERO_API_BASE}/Invoices/{invoice_id}",
            headers=request_headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        _handle_request_exception(sync, bill, user_id, payload, request_headers, exc)
        raise BillValidationError(f"Xero API call failed: {exc}")

    sync = _update_sync_with_response(sync, response, payload, request_headers)

    if sync.sync_status == XeroBillSync.SyncStatus.SUCCESS:
        bill.published = Bill.PublishStatus.PUBLISHED
        bill.save(update_fields=["published", "status", "updated_at"])
        log_audit(
            bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
            f"Updated Xero invoice #{sync.response_invoice_number}",
        )
        _upload_bill_attachments_to_xero(bill, sync, str(entity.id), user_id, xero_org_id)
        _upload_existing_bankslips_to_xero(bill, str(entity.id), xero_org_id, user_id)
    else:
        bill.published = Bill.PublishStatus.FAILED
        bill.save(update_fields=["published", "updated_at"])
        log_audit(
            bill, Audit.Action.PUBLISHED_TO_XERO, user_id,
            f"Update failed: {sync.error_message[:200]}",
        )
        raise BillValidationError(f"Xero rejected the update: {sync.error_message}")

    logger.info("_update_bill_to_xero: completed bill=%s sync=%s", bill.id, sync.id)
    return {"bill": bill, "sync_id": str(sync.id)}


# ─── Helpers ──────────────────────────────────────────────────────────────


def _load_bill(bill_id: str, entity_id: str) -> Bill:
    try:
        return Bill.objects.prefetch_related(
            "line_items", "bill_attachments__attachment",
        ).get(id=bill_id, entity_id=entity_id)
    except Bill.DoesNotExist:
        raise BillValidationError("Bill not found")


def _load_entity(entity_id: str) -> Entity:
    try:
        return Entity.objects.get(id=entity_id)
    except Entity.DoesNotExist:
        raise BillValidationError("Entity not found")


def _build_xero_invoice_payload(bill: Bill, entity: Entity) -> dict:
    """Build the Xero Invoices payload from bill data."""

    xero_status = "AUTHORISED"
    if bill.invoice_date:
        if entity.period_lock_date and bill.invoice_date <= entity.period_lock_date:
            xero_status = "DRAFT"
            logger.info(
                "bill=%s invoice_date=%s on/before period_lock_date=%s → DRAFT",
                bill.id, bill.invoice_date, entity.period_lock_date,
            )
        elif entity.end_of_year_lock_date and bill.invoice_date <= entity.end_of_year_lock_date:
            xero_status = "DRAFT"
            logger.info(
                "bill=%s invoice_date=%s on/before end_of_year_lock_date=%s → DRAFT",
                bill.id, bill.invoice_date, entity.end_of_year_lock_date,
            )

    line_items = []
    for li in bill.line_items.all().order_by("sort_order"):
        line_items.append({
            "Description": li.description or bill.description or "",
            "Quantity": float(li.quantity),
            "UnitAmount": float(li.unit_amount),
            "AccountCode": li.account_code or bill.xero_account_code or "",
            "TaxType": li.tax_type or "NONE",
            "LineAmount": float(li.line_amount),
        })

    if not line_items:
        line_items.append({
            "Description": bill.description or "",
            "Quantity": 1,
            "UnitAmount": float(bill.amount),
            "AccountCode": bill.xero_account_code or "",
            "TaxType": "NONE",
            "LineAmount": float(bill.amount),
        })

    invoice = {
        "Type": "ACCPAY",
        "Contact": {"ContactID": bill.xero_contact_id},
        "LineItems": line_items,
        "Date": bill.invoice_date.isoformat() if bill.invoice_date else "",
        "DueDate": bill.due_date.isoformat() if bill.due_date else "",
        "Reference": bill.description or "",
        "InvoiceNumber": bill.reference or "",
        "Status": xero_status,
    }

    return {"Invoices": [invoice]}


def _create_sync_record(
    bill: Bill, user_id: str, idempotency_key: str, *, is_republish: bool = False,
) -> XeroBillSync:
    """Create XeroBillSync + XeroBillSyncLine snapshot rows."""

    sync_type = (
        XeroBillSync.SyncType.UPDATE_INVOICE if is_republish
        else XeroBillSync.SyncType.CREATE_INVOICE
    )

    with transaction.atomic():
        sync = XeroBillSync.objects.create(
            bill=bill,
            sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
            sync_type=sync_type,
            sync_status=XeroBillSync.SyncStatus.PENDING,
            request_type="ACCPAY",
            request_status="AUTHORISED",
            request_contact_id=bill.xero_contact_id or "",
            request_invoice_number=bill.reference or "",
            request_reference=bill.description or "",
            request_invoice_date=bill.invoice_date,
            request_due_date=bill.due_date,
            idempotency_key=idempotency_key,
            requested_by=user_id,
            requested_at=datetime.now(timezone.utc),
        )

        for li in bill.line_items.all().order_by("sort_order"):
            XeroBillSyncLine.objects.create(
                xero_bill_sync=sync,
                bill_line_item=li,
                description=li.description,
                quantity=li.quantity,
                unit_amount=li.unit_amount,
                line_amount=li.line_amount,
                account_code=li.account_code or bill.xero_account_code or "",
                tax_type=li.tax_type or "NONE",
                sort_order=li.sort_order,
            )

    return sync


def _handle_request_exception(sync, bill, user_id, payload, request_headers, exc):
    """Persist failure when the HTTP request itself raised."""

    sync.sync_status = XeroBillSync.SyncStatus.FAILED
    sync.has_errors = True
    sync.error_message = str(exc)[:1000]
    sync.responded_at = datetime.now(timezone.utc)
    sync.save()

    XeroBillSyncPayload.objects.create(
        xero_bill_sync=sync,
        request_json=payload,
        response_json={"error": str(exc)},
        request_headers=_sanitise_headers(request_headers),
    )

    bill.published = Bill.PublishStatus.FAILED
    bill.save(update_fields=["published", "updated_at"])
    log_audit(bill, Audit.Action.PUBLISHED_TO_XERO, user_id, f"Publish failed: {exc}")


def _update_sync_with_response(sync, response, request_payload, request_headers):
    """Parse the Xero response, update sync record, create payload + response-line rows."""

    now = datetime.now(timezone.utc)
    response_headers_dict = dict(response.headers) if response.headers else {}

    try:
        response_json = response.json()
    except Exception:
        response_json = {"raw": response.text[:5000]}

    sync.http_status_code = response.status_code
    sync.responded_at = now

    if response.status_code in (200, 201):
        invoices = response_json.get("Invoices", [])
        if invoices:
            _apply_success(sync, invoices[0], response_json)
        else:
            sync.sync_status = XeroBillSync.SyncStatus.FAILED
            sync.has_errors = True
            sync.error_message = "No invoices returned in Xero response"
    else:
        sync.sync_status = XeroBillSync.SyncStatus.FAILED
        sync.has_errors = True
        sync.error_message = _extract_xero_error(response_json, response.text)

    sync.save()

    XeroBillSyncPayload.objects.create(
        xero_bill_sync=sync,
        request_json=request_payload,
        response_json=response_json,
        request_headers=_sanitise_headers(request_headers),
        response_headers=response_headers_dict,
    )

    return sync


def _apply_success(sync, inv: dict, response_json: dict):
    """Populate sync fields from a successful Xero invoice response."""

    sync.sync_status = XeroBillSync.SyncStatus.SUCCESS
    sync.response_invoice_id = inv.get("InvoiceID", "")
    sync.response_invoice_number = inv.get("InvoiceNumber", "")
    sync.response_status = inv.get("Status", "")
    sync.response_amount_due = Decimal(str(inv.get("AmountDue", 0)))
    sync.response_amount_paid = Decimal(str(inv.get("AmountPaid", 0)))
    sync.response_total = Decimal(str(inv.get("Total", 0)))
    sync.response_currency_code = inv.get("CurrencyCode", "")
    sync.xero_response_id = inv.get("InvoiceID", "")
    sync.xero_provider_name = response_json.get("ProviderName", "")
    sync.xero_datetime_utc = response_json.get("DateTimeUTC", "")
    sync.has_errors = False

    for xli in inv.get("LineItems", []):
        XeroBillResponseLine.objects.create(
            xero_bill_sync=sync,
            xero_line_item_id=xli.get("LineItemID", ""),
            description=xli.get("Description", ""),
            quantity=Decimal(str(xli.get("Quantity", 0))),
            unit_amount=Decimal(str(xli.get("UnitAmount", 0))),
            line_amount=Decimal(str(xli.get("LineAmount", 0))),
            tax_type=xli.get("TaxType", ""),
            tax_amount=Decimal(str(xli.get("TaxAmount", 0))),
            account_code=xli.get("AccountCode", ""),
            account_id=xli.get("AccountID", ""),
            validation_errors=xli.get("ValidationErrors"),
        )


def _extract_xero_error(response_json, raw_text: str) -> str:
    """Pull a human-readable error from the Xero response body."""

    if not isinstance(response_json, dict):
        return str(response_json)[:1000]

    parts: list[str] = []
    for elem in response_json.get("Elements", []):
        for ve in elem.get("ValidationErrors", []):
            parts.append(ve.get("Message", ""))
    if parts:
        return "; ".join(parts)

    return response_json.get("Message", raw_text[:1000])


def _sanitise_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() != "authorization"}


# ─── Attachment upload / sync (Xero Files API) ───────────────────────────


def _upload_bill_attachments_to_xero(bill, sync, entity_id, user_id, xero_org_id):
    """Authoritative full-replace sync of bill attachments to the Xero invoice.

    Uses the Xero Files API (files.xro/1.0) for upload, association, and deletion.

    Every publish/republish is treated as a clean slate:
      1. Query Xero directly for all files currently associated with the invoice
         (GET /files.xro/1.0/Files/Associations/{InvoiceId}).
      2. DELETE each file from Xero Files API (DELETE /files.xro/1.0/Files/{FileId}).
         This is authoritative — does not rely on local xero_attachment_id tracking,
         so deletions work correctly even when BillAttachment rows were hard-deleted
         between publishes.
      3. Reset xero_attachment_id and xero_filename to "" on all BillAttachment rows.
      4. For each current BillAttachment: download from S3, upload via Files API
         multipart POST, associate to the invoice, then store the FileId on the row.
    """

    invoice_id = sync.response_invoice_id
    if not invoice_id:
        return

    invoice_number = sync.response_invoice_number or ""
    access_token = resolve_xero_access_token_for_entity(entity_id, user_id)

    # Deduplicate: if two BillAttachment rows share the same Attachment, only keep one.
    seen_attachment_ids: set[str] = set()
    bill_attachments_qs = (
        bill.bill_attachments
        .select_related("attachment")
        .order_by("created_at")
    )

    # Step 1 & 2: Collect all FileIds that need to be removed from Xero.
    #
    # Two sources are merged so deletion is resilient to either one failing:
    #  a) Locally tracked FileIds on BillAttachment rows — fast, works even when
    #     the Xero Files/Associations endpoint returns 404 (which it does when no
    #     files have ever been associated to this invoice).
    #  b) The Xero Files API associations query — authoritative, catches orphaned
    #     files whose BillAttachment rows were hard-deleted between publishes.
    local_file_ids = {
        ba.xero_attachment_id
        for ba in bill_attachments_qs
        if ba.xero_attachment_id
    }

    xero_file_ids = {
        info["FileId"]
        for info in _get_xero_files_for_invoice(access_token, xero_org_id, invoice_id)
        if info.get("FileId")
    }

    all_file_ids_to_delete = local_file_ids | xero_file_ids
    logger.info(
        "Xero attachment sync: deleting %d file(s) from Xero for invoice=%s "
        "(local=%d xero_api=%d)",
        len(all_file_ids_to_delete), invoice_id,
        len(local_file_ids), len(xero_file_ids),
    )
    for file_id in all_file_ids_to_delete:
        _delete_xero_file(access_token, xero_org_id, file_id)

    # Step 3: Reset local tracking fields.
    bill_attachments_qs.update(xero_attachment_id="", xero_filename="")

    # If the bill has no attachments there is nothing left to upload.
    if not bill_attachments_qs.exists():
        return

    # Step 4: Re-fetch and upload each active attachment with a slot-based filename.
    bill_attachments = list(bill_attachments_qs.all())
    s3 = _get_s3_client()

    logger.info(
        "Xero attachment sync: %d bill_attachment row(s) found for invoice=%s",
        len(bill_attachments), invoice_id,
    )
    for ba in bill_attachments:
        logger.info(
            "Xero attachment sync: bill_attachment=%s attachment=%s name=%s path=%s",
            ba.id, ba.attachment_id, ba.attachment.original_name, ba.attachment.file_path,
        )

    upload_idx = 0
    for ba in bill_attachments:
        att = ba.attachment

        # Skip if a different BillAttachment row already uploaded this same Attachment.
        if str(att.id) in seen_attachment_ids:
            logger.warning(
                "Xero attachment sync: skipping duplicate attachment=%s (already uploaded via another row)",
                att.id,
            )
            continue
        seen_attachment_ids.add(str(att.id))

        if att.is_deleted:
            logger.info("Xero attachment sync: skipping deleted attachment=%s", att.id)
            continue

        file_bytes = _download_from_s3(s3, att)
        if file_bytes is None:
            logger.warning(
                "Xero attachment sync: S3 download returned None attachment=%s path=%s",
                att.id, att.file_path,
            )
            continue

        ext = att.file_extension or (
            att.original_name.rsplit(".", 1)[-1] if "." in att.original_name else "bin"
        )
        suffix = f"_{upload_idx}" if upload_idx > 0 else ""
        raw_filename = f"{invoice_number}_PAYMENTREQUEST{suffix}.{ext}"
        xero_filename = _sanitize_xero_filename(raw_filename)
        content_type = _resolve_xero_content_type(att.mime_type, ext)

        logger.info(
            "Xero attachment sync: uploading slot=%d file=%s content_type=%s size=%d bytes",
            upload_idx, xero_filename, content_type, len(file_bytes),
        )

        file_id = _upload_and_associate_bill_attachment(
            access_token, xero_org_id, invoice_id, xero_filename, file_bytes, content_type,
        )

        upload_idx += 1

        if file_id:
            ba.xero_attachment_id = file_id
            ba.xero_filename = xero_filename
            ba.save(update_fields=["xero_attachment_id", "xero_filename", "updated_at"])
            logger.info(
                "Xero attachment sync: fully synced attachment=%s file_id=%s filename=%s",
                att.id, file_id, xero_filename,
            )
        else:
            logger.warning(
                "Xero attachment sync: upload FAILED for file=%s", xero_filename,
            )


def _get_xero_files_for_invoice(
    access_token: str, xero_org_id: str, invoice_id: str,
) -> list[dict]:
    """GET /files.xro/1.0/Files/Associations/{InvoiceId}.

    Returns a list of {FileId, Name} dicts for all files associated with the invoice.
    """

    if not access_token:
        return []

    url = f"{XERO_FILES_API_BASE}/Files/Associations/{invoice_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                result = []
                for item in data:
                    # Xero may return FileId or Id depending on endpoint version.
                    file_id = item.get("FileId") or item.get("Id") or ""
                    name = item.get("Name") or item.get("FileName") or ""
                    if file_id:
                        result.append({"FileId": file_id, "Name": name})
                return result
        if resp.status_code == 404:
            # Xero returns 404 (not an empty list) when the invoice has no
            # associated files yet. Treat it as an empty result, not an error.
            logger.info(
                "Xero files list invoice=%s returned 404 (no prior associations)",
                invoice_id,
            )
            return []
        logger.warning(
            "Xero files list failed invoice=%s status=%s body=%s",
            invoice_id, resp.status_code, resp.text[:300],
        )
        return []
    except requests.RequestException as exc:
        logger.error("Xero files list request failed invoice=%s: %s", invoice_id, exc)
        return []


def _upload_and_associate_bill_attachment(
    access_token: str,
    xero_org_id: str,
    invoice_id: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> str | None:
    """Upload a file to the Xero Files API and associate it with the invoice.

    Returns the FileId on success, or None on any failure.
    """

    file_id = _upload_file_to_xero_files_api(
        access_token, xero_org_id, filename, file_bytes, content_type,
    )
    if not file_id:
        return None

    associated = _associate_file_to_xero_invoice(
        access_token, xero_org_id, file_id, invoice_id,
    )
    if associated:
        return file_id

    logger.warning(
        "Xero attachment sync: association failed for file_id=%s invoice=%s",
        file_id, invoice_id,
    )
    return None


def _delete_xero_file(access_token: str, xero_org_id: str, file_id: str) -> None:
    """DELETE /files.xro/1.0/Files/{FileId} — removes the file from Xero entirely."""

    if not access_token or not file_id:
        return

    url = f"{XERO_FILES_API_BASE}/Files/{file_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
    }

    try:
        resp = requests.delete(url, headers=headers, timeout=30)
        if resp.status_code in (200, 204):
            logger.info("Xero file deleted file_id=%s", file_id)
        else:
            logger.warning(
                "Xero file delete failed file_id=%s status=%s body=%s",
                file_id, resp.status_code, resp.text[:500],
            )
    except requests.RequestException as exc:
        logger.error("Xero file delete request failed file_id=%s: %s", file_id, exc)


def _upload_file_to_xero_files_api(
    access_token: str,
    xero_org_id: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> str | None:
    """POST multipart file to /files.xro/1.0/Files and return the FileId."""

    if not access_token:
        logger.warning("Skipping Xero file upload — no access token")
        return None

    url = f"{XERO_FILES_API_BASE}/Files"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Accept": "application/json",
    }
    # Xero Files API: part name must match the filename (including extension).
    files = {filename: (filename, file_bytes, content_type)}

    try:
        resp = requests.post(url, headers=headers, files=files, timeout=60)
        if resp.status_code in (200, 201):
            data = resp.json()
            file_id = data.get("FileId") or data.get("Id") or ""
            if file_id:
                logger.info("Xero file uploaded filename=%s file_id=%s", filename, file_id)
                return file_id
            logger.warning(
                "Xero file upload succeeded but no FileId returned filename=%s body=%s",
                filename, resp.text[:500],
            )
            return None
        logger.warning(
            "Xero file upload failed filename=%s status=%s body=%s",
            filename, resp.status_code, resp.text[:500],
        )
        return None
    except requests.RequestException as exc:
        logger.error("Xero file upload request failed filename=%s: %s", filename, exc)
        return None


def _associate_file_to_xero_invoice(
    access_token: str, xero_org_id: str, file_id: str, invoice_id: str,
) -> bool:
    """POST /files.xro/1.0/Files/{FileId}/Associations to link the file to an invoice."""

    if not access_token:
        logger.warning("Skipping Xero file association — no access token")
        return False

    url = f"{XERO_FILES_API_BASE}/Files/{file_id}/Associations"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": xero_org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        "ObjectId": invoice_id,
        "ObjectGroup": "Invoice",
        "ObjectType": "ACCPAY",
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code in (200, 201):
            logger.info("Xero file associated file_id=%s invoice_id=%s", file_id, invoice_id)
            return True
        logger.warning(
            "Xero file association failed file_id=%s invoice_id=%s status=%s body=%s",
            file_id, invoice_id, resp.status_code, resp.text[:500],
        )
        return False
    except requests.RequestException as exc:
        logger.error(
            "Xero file association request failed file_id=%s invoice_id=%s: %s",
            file_id, invoice_id, exc,
        )
        return False


def _download_from_s3(s3, attachment) -> bytes | None:
    try:
        obj = s3.get_object(Bucket=settings.S3_BUCKET, Key=attachment.file_path)
        return obj["Body"].read()
    except ClientError as exc:
        logger.error(
            "S3 download failed attachment=%s path=%s: %s",
            attachment.id, attachment.file_path, exc,
        )
        return None


# ─── Bank-slip upload to Xero ─────────────────────────────────────────────


def _sync_org(sync: XeroBillSync) -> str:
    """Xero org a sync was published to, or "" when unrecorded.

    There is no column for this. Every publish that reaches Xero stores its
    request headers on the sync's payload row (_update_sync_with_response,
    _handle_request_exception) and _sanitise_headers strips only
    Authorization, so Xero-Tenant-Id survives there. The payload is therefore
    the record of which organisation a sync targeted.
    """
    payload = getattr(sync, "payload", None)  # reverse OneToOne; may not exist
    headers = (payload.request_headers or {}) if payload else {}
    return headers.get("Xero-Tenant-Id") or ""


def _select_successful_sync(bill_id: str, xero_org_id):
    """Pick the sync to republish against.

    Returns ``(sync_for_this_org, sync_for_another_org)``.

    An entity can be reconnected to a different Xero organisation. An invoice
    id from a sync made under the old org does not exist in the new one, so
    reusing it sends a valid tenant header with a foreign invoice id: Xero
    rejects it and the bill can never be published again. Skipping the
    mismatched sync makes the caller fall through to the create path and
    publish the bill fresh into the current org.

    A sync with no recorded org ("") is treated as belonging to this one,
    preserving existing behaviour for rows written before the payload carried
    headers. Treating unknown as a mismatch would create a duplicate invoice
    in the SAME org, which is worse than the stuck state this guards against.
    """
    candidates = (
        XeroBillSync.objects
        .filter(bill_id=bill_id, sync_status=XeroBillSync.SyncStatus.SUCCESS)
        .select_related("payload")
        .order_by("-requested_at", "-created_at")
    )

    foreign = None
    for sync in candidates:
        sync_org = _sync_org(sync)
        if not sync_org or sync_org == str(xero_org_id):
            return sync, None
        if foreign is None:
            foreign = sync
    return None, foreign


def _get_latest_successful_sync(bill_id: str, xero_org_id) -> XeroBillSync | None:
    """Most recent successful sync for a bill in the given Xero org, or None."""
    sync, _foreign = _select_successful_sync(bill_id, xero_org_id)
    return sync


def _reset_for_new_org(bill: Bill, foreign_sync: XeroBillSync, xero_org_id) -> None:
    """Prepare a bill whose last publish went to a different Xero org.

    The bill is about to be created fresh in the current org, so anything on
    it naming an object in the old one has to go, or be checked first.
    """
    logger.info(
        "Bill %s was last published to Xero org %s but entity is now on %s; "
        "publishing fresh into the current org",
        bill.id, _sync_org(foreign_sync) or "unknown", xero_org_id,
    )

    if bill.xero_contact_id:
        bill.xero_contact_id = ""
        bill.save(update_fields=["xero_contact_id"])

    _assert_account_codes_exist(bill)


def _assert_account_codes_exist(bill: Bill) -> None:
    """Fail early when a bill names an account code the new org does not have.

    Account codes are copied onto the bill at save time (bill_service) and
    read straight back out by _build_xero_invoice_payload; nothing resolves
    them live. After an org switch they can name accounts that do not exist,
    and Xero's own rejection does not tell the user what to fix.

    Deliberately does NOT remap. Codes are user-defined strings that collide
    across organisations, so a same-numbered account in the new org may be
    something entirely different. A wrong ledger entry is worse than a
    blocked publish; the user re-selects the account.
    """
    codes = {
        (li.account_code or bill.xero_account_code or "").strip()
        for li in bill.line_items.all()
    }
    if not codes:
        codes = {(bill.xero_account_code or "").strip()}
    codes = {c for c in codes if c}
    if not codes:
        return

    valid = set(
        EntityBillAccountXero.objects
        .filter(entity_id=bill.entity_id, is_active=True, is_deleted=False)
        .values_list("account_code", flat=True)
    )
    missing = sorted(codes - valid)
    if missing:
        raise BillValidationError(
            "Account code {} does not exist in the Xero organisation this "
            "entity is now connected to. Re-select the account on this bill, "
            "then publish again.".format(", ".join(missing))
        )


def upload_bankslip_to_xero(
    bill_id: str, payment_id: str, entity_id: str, user_id: str, access_token: str,
) -> dict:
    """Authoritative full-replace sync of a payment's bank-slip attachments
    to its Xero invoice.

    Every call is treated as a clean slate so repeated publishes/republishes do
    not pile duplicate copies of the same bank-slip file onto the invoice:

      1. Collect every Xero FileId previously uploaded for this payment from
         two sources — locally tracked PaymentAttachment.xero_attachment_id
         values and any file currently associated with the invoice whose name
         matches the ``*_BANKSLIP*`` pattern (defence in depth, in case the
         local tracking row was lost).  The filename filter prevents this step
         from ever deleting bill attachments uploaded in the same publish
         cycle by ``_upload_bill_attachments_to_xero``.
      2. DELETE each of those FileIds from the Xero Files API.
      3. Reset the local xero_attachment_id / xero_filename fields.
      4. For each current PaymentAttachment, download from S3, upload via the
         Files API, associate to the invoice, and persist the new FileId on
         the PaymentAttachment row so the next call can clean it up.
    """

    logger.info(
        "upload_bankslip_to_xero: starting bill=%s payment=%s", bill_id, payment_id,
    )

    bill = _load_bill(bill_id, entity_id)
    if bill.published != Bill.PublishStatus.PUBLISHED:
        raise BillValidationError("Bill is not published to Xero yet.")

    entity = _load_entity(entity_id)
    xero_org_id = entity.xero_org_id
    if not xero_org_id:
        raise BillValidationError("Entity has no Xero organization linked")
    if not access_token:
        raise BillValidationError("No Xero access token available. Please reconnect to Xero.")

    sync = _get_latest_successful_sync(bill_id, xero_org_id)
    if not sync or not sync.response_invoice_id:
        raise BillValidationError("No successful Xero sync found for this bill.")

    invoice_id = sync.response_invoice_id
    invoice_number = sync.response_invoice_number or bill.reference or "UNKNOWN"

    try:
        payment = Payment.objects.get(id=payment_id, bill_id=bill_id)
    except Payment.DoesNotExist:
        raise BillValidationError("Payment not found")

    payment_attachments_qs = (
        PaymentAttachment.objects
        .filter(payment=payment)
        .select_related("attachment")
        .order_by("created_at")
    )

    if not payment_attachments_qs.exists():
        raise BillValidationError("No attachments found on this payment.")

    idempotency_key = str(uuid.uuid4())
    bankslip_sync = _create_bankslip_sync_record(bill, user_id, idempotency_key, sync)

    # Step 1 & 2: collect prior bank-slip FileIds and delete them from Xero
    # before re-uploading.  Two sources are merged for resilience.
    local_file_ids = {
        pa.xero_attachment_id
        for pa in payment_attachments_qs
        if pa.xero_attachment_id
    }
    xero_file_ids = {
        info["FileId"]
        for info in _get_xero_files_for_invoice(access_token, xero_org_id, invoice_id)
        if info.get("FileId") and _is_bankslip_filename(info.get("Name", ""))
    }
    file_ids_to_delete = local_file_ids | xero_file_ids
    logger.info(
        "Xero bankslip sync: deleting %d prior bank-slip file(s) for invoice=%s "
        "(local=%d xero_api=%d)",
        len(file_ids_to_delete), invoice_id,
        len(local_file_ids), len(xero_file_ids),
    )
    for file_id in file_ids_to_delete:
        _delete_xero_file(access_token, xero_org_id, file_id)

    # Step 3: reset local tracking before uploading fresh copies.
    payment_attachments_qs.update(xero_attachment_id="", xero_filename="")

    # Step 4: re-upload each active attachment with a slot-based filename.
    payment_attachments = list(payment_attachments_qs.all())
    s3 = _get_s3_client()
    uploaded_count = 0

    upload_idx = 0
    for pa in payment_attachments:
        att = pa.attachment
        if att.is_deleted:
            logger.info("Xero bankslip sync: skipping deleted attachment=%s", att.id)
            continue

        file_bytes = _download_from_s3(s3, att)
        if file_bytes is None:
            logger.warning(
                "Xero bankslip sync: S3 download returned None attachment=%s path=%s",
                att.id, att.file_path,
            )
            continue

        ext = att.file_extension or (
            att.original_name.rsplit(".", 1)[-1]
            if att.original_name and "." in att.original_name
            else "pdf"
        )
        suffix = f"_{upload_idx}" if upload_idx > 0 else ""
        raw_filename = f"{invoice_number}_BANKSLIP{suffix}.{ext}"
        xero_filename = _sanitize_xero_filename(raw_filename)
        content_type = _resolve_xero_content_type(att.mime_type, ext)

        logger.info(
            "Xero bankslip sync: uploading slot=%d file=%s content_type=%s size=%d bytes",
            upload_idx, xero_filename, content_type, len(file_bytes),
        )

        file_id = _upload_and_associate_bill_attachment(
            access_token, xero_org_id, invoice_id,
            xero_filename, file_bytes, content_type,
        )

        upload_idx += 1

        if file_id:
            pa.xero_attachment_id = file_id
            pa.xero_filename = xero_filename
            pa.save(update_fields=["xero_attachment_id", "xero_filename", "updated_at"])
            uploaded_count += 1
            logger.info(
                "Xero bankslip sync: fully synced attachment=%s file_id=%s filename=%s",
                att.id, file_id, xero_filename,
            )
        else:
            logger.warning(
                "Xero bankslip sync: upload FAILED for file=%s", xero_filename,
            )

    _finalise_bankslip_sync(bankslip_sync, uploaded_count, bill, user_id)

    logger.info(
        "upload_bankslip_to_xero: completed bill=%s payment=%s uploaded=%s",
        bill_id, payment_id, uploaded_count,
    )
    return {"uploaded": uploaded_count, "sync_id": str(bankslip_sync.id)}


def _is_bankslip_filename(name: str) -> bool:
    """Return True when a Xero file name was uploaded by the bank-slip sync.

    Used to scope deletion in upload_bankslip_to_xero so it never accidentally
    removes bill attachments uploaded earlier in the same publish cycle by
    _upload_bill_attachments_to_xero (which uses the ``_PAYMENTREQUEST`` tag).
    Match is case-insensitive on the ``_BANKSLIP`` substring.
    """
    return "_bankslip" in (name or "").lower()


def _create_bankslip_sync_record(bill, user_id, idempotency_key, parent_sync):
    """Create a sync record for the bankslip attachment upload."""

    sync = XeroBillSync.objects.create(
        bill=bill,
        sync_direction=XeroBillSync.SyncDirection.OUTBOUND,
        sync_type=XeroBillSync.SyncType.UPDATE_INVOICE,
        sync_status=XeroBillSync.SyncStatus.PENDING,
        request_type="ATTACHMENT_BANKSLIP",
        request_status="",
        request_contact_id=bill.xero_contact_id or "",
        request_invoice_number=bill.reference or "",
        request_reference=bill.description or "",
        request_invoice_date=bill.invoice_date,
        request_due_date=bill.due_date,
        response_invoice_id=parent_sync.response_invoice_id,
        response_invoice_number=parent_sync.response_invoice_number,
        idempotency_key=idempotency_key,
        requested_by=user_id,
        requested_at=datetime.now(timezone.utc),
    )
    return sync


def _finalise_bankslip_sync(sync, uploaded_count, bill, user_id):
    """Update the bankslip sync record with the result."""

    sync.responded_at = datetime.now(timezone.utc)

    if uploaded_count > 0:
        sync.sync_status = XeroBillSync.SyncStatus.SUCCESS
        sync.has_errors = False
        sync.error_message = ""
        log_audit(
            bill, Audit.Action.ATTACHMENT_UPLOADED, user_id,
            f"Bank slip uploaded to Xero invoice #{sync.response_invoice_number}",
        )
    else:
        sync.sync_status = XeroBillSync.SyncStatus.FAILED
        sync.has_errors = True
        sync.error_message = "No bank slip files could be uploaded to Xero"
        log_audit(
            bill, Audit.Action.ATTACHMENT_UPLOADED, user_id,
            "Bank slip upload to Xero failed",
        )

    sync.save()

    XeroBillSyncPayload.objects.create(
        xero_bill_sync=sync,
        request_json={"action": "upload_bankslip", "invoice_id": sync.response_invoice_id},
        response_json={"uploaded_count": uploaded_count},
        request_headers={},
        response_headers={},
    )


def _upload_existing_bankslips_to_xero(bill, entity_id, xero_org_id, user_id):
    """After publishing, upload any existing payment attachments to Xero."""

    payments = Payment.objects.filter(bill=bill)
    if not payments.exists():
        return

    for payment in payments:
        pa_qs = PaymentAttachment.objects.filter(payment=payment).select_related("attachment")
        if not pa_qs.exists():
            continue

        try:
            fresh_token = resolve_xero_access_token_for_entity(entity_id, user_id)
            upload_bankslip_to_xero(
                bill_id=str(bill.id),
                payment_id=str(payment.id),
                entity_id=entity_id,
                user_id=user_id,
                access_token=fresh_token or "",
            )
        except Exception as exc:
            logger.warning(
                "Auto bankslip upload failed bill=%s payment=%s: %s",
                bill.id, payment.id, exc,
            )


def upload_bankslip_background(bill_id, payment_id, entity_id, user_id, access_token):
    """Fire-and-forget bankslip upload in a background thread."""

    def _run():
        try:
            connection.close()
            upload_bankslip_to_xero(bill_id, payment_id, entity_id, user_id, access_token)
        except Exception as exc:
            logger.warning(
                "Background bankslip upload failed bill=%s payment=%s: %s",
                bill_id, payment_id, exc,
            )
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info(
        "upload_bankslip_background: spawned thread bill=%s payment=%s",
        bill_id, payment_id,
    )
