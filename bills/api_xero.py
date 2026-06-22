import logging

from django.http import Http404
from ninja import Router

from bills.models import Bill, XeroBillSync, XeroBillSyncPayload
from bills.schemas import (
    ErrorOut,
    XeroBillSyncListOut,
    XeroBillSyncOut,
)

logger = logging.getLogger("minty-api")


def _get_bill_or_404(bill_id: str, entity_id: str) -> Bill:
    try:
        return Bill.objects.get(id=bill_id, entity_id=entity_id)
    except Bill.DoesNotExist:
        raise Http404("Bill not found")


def _sync_to_out(sync: XeroBillSync) -> dict:
    sync_lines = [
        {
            "id": str(sl.id),
            "bill_line_item_id": str(sl.bill_line_item_id) if sl.bill_line_item_id else None,
            "description": sl.description,
            "quantity": sl.quantity,
            "unit_amount": sl.unit_amount,
            "line_amount": sl.line_amount,
            "account_code": sl.account_code,
            "tax_type": sl.tax_type,
            "sort_order": sl.sort_order,
            "response_line_item_id": sl.response_line_item_id,
            "response_account_id": sl.response_account_id,
            "response_tax_amount": sl.response_tax_amount,
            "created_at": sl.created_at,
        }
        for sl in sync.sync_lines.all()
    ]

    payload = None
    try:
        p = sync.payload
        payload = {
            "id": str(p.id),
            "request_json": p.request_json,
            "response_json": p.response_json,
            "request_headers": p.request_headers,
            "response_headers": p.response_headers,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
    except XeroBillSyncPayload.DoesNotExist:
        pass

    response_lines = [
        {
            "id": str(rl.id),
            "xero_line_item_id": rl.xero_line_item_id,
            "description": rl.description,
            "quantity": rl.quantity,
            "unit_amount": rl.unit_amount,
            "line_amount": rl.line_amount,
            "tax_type": rl.tax_type,
            "tax_amount": rl.tax_amount,
            "account_code": rl.account_code,
            "account_id": rl.account_id,
            "validation_errors": rl.validation_errors,
            "created_at": rl.created_at,
        }
        for rl in sync.response_lines.all()
    ]

    return {
        "id": str(sync.id),
        "bill_id": str(sync.bill_id),
        "sync_direction": sync.sync_direction,
        "sync_type": sync.sync_type,
        "sync_status": sync.sync_status,
        "request_type": sync.request_type,
        "request_status": sync.request_status,
        "request_contact_id": sync.request_contact_id,
        "request_invoice_number": sync.request_invoice_number,
        "request_reference": sync.request_reference,
        "request_invoice_date": sync.request_invoice_date,
        "request_due_date": sync.request_due_date,
        "response_invoice_id": sync.response_invoice_id,
        "response_invoice_number": sync.response_invoice_number,
        "response_status": sync.response_status,
        "response_amount_due": sync.response_amount_due,
        "response_amount_paid": sync.response_amount_paid,
        "response_total": sync.response_total,
        "response_currency_code": sync.response_currency_code,
        "xero_response_id": sync.xero_response_id,
        "xero_provider_name": sync.xero_provider_name,
        "xero_datetime_utc": sync.xero_datetime_utc,
        "http_status_code": sync.http_status_code,
        "idempotency_key": sync.idempotency_key,
        "retry_count": sync.retry_count,
        "last_retry_at": sync.last_retry_at,
        "has_errors": sync.has_errors,
        "error_message": sync.error_message,
        "requested_by": sync.requested_by,
        "requested_at": sync.requested_at,
        "responded_at": sync.responded_at,
        "created_at": sync.created_at,
        "updated_at": sync.updated_at,
        "sync_lines": sync_lines,
        "payload": payload,
        "response_lines": response_lines,
    }


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNCS (read-only)
# ═══════════════════════════════════════════════════════════════════════════

xero_syncs_router = Router()


@xero_syncs_router.get(
    "/{bill_id}/xero-syncs",
    response={200: list[XeroBillSyncListOut], 404: ErrorOut},
    summary="List xero syncs for a bill",
)
def list_xero_syncs(request, bill_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    return list(XeroBillSync.objects.filter(bill=bill).order_by("-created_at"))


@xero_syncs_router.get(
    "/{bill_id}/xero-syncs/{sync_id}",
    response={200: XeroBillSyncOut, 404: ErrorOut},
    summary="Get xero sync detail with lines, payload, and response lines",
)
def get_xero_sync(request, bill_id: str, sync_id: str):
    bill = _get_bill_or_404(bill_id, request.entity_id)
    try:
        sync = XeroBillSync.objects.prefetch_related(
            "sync_lines", "response_lines"
        ).select_related("payload").get(id=sync_id, bill=bill)
    except XeroBillSync.DoesNotExist:
        raise Http404("Xero sync not found")
    return _sync_to_out(sync)
