import logging
from decimal import Decimal

from django.db import transaction

from bills.models import Audit, Bill, BillLineItem
from bills.services.audit_service import log_audit
from bills.services.bill_reference_generator import generate_unique_bill_reference
from bills.services.payment_service import sync_bill_status_from_payments
from core.exceptions import BillValidationError
from shared_models.models import User

logger = logging.getLogger("minty-api")

_FIELD_LABELS = {
    "contact": "Contact",
    "description": "Description",
    "amount": "Amount",
    "due_date": "Due date",
    "invoice_date": "Invoice date",
    "reference": "Reference",
    "currency_code": "Currency",
    "status": "Status",
    "xero_contact_id": "Xero contact",
}

_STATUSES_EXCLUDED_FROM_REFERENCE_UNIQUENESS = (
    Bill.Status.VOIDED,
    Bill.Status.CANCELLED,
)


def _reference_unchanged_for_bill(bill: Bill, reference: str | None) -> bool:
    """True if the incoming reference is the same as the bill's (trim + case-insensitive)."""
    return (reference or "").strip().lower() == (bill.reference or "").strip().lower()


def _ensure_reference_unique(
    entity_id: str,
    reference: str | None,
    *,
    exclude_bill_id: str | None = None,
) -> None:
    """Reject duplicate invoice/bill reference within the entity (case-insensitive). Skips empty refs."""
    ref = (reference or "").strip()
    if not ref:
        return
    qs = (
        Bill.objects.filter(entity_id=entity_id)
        .exclude(status__in=_STATUSES_EXCLUDED_FROM_REFERENCE_UNIQUENESS)
        .filter(reference__iexact=ref)
    )
    if exclude_bill_id:
        qs = qs.exclude(pk=exclude_bill_id)
    if qs.exists():
        logger.warning(
            "Duplicate bill reference rejected entity_id=%s reference=%s exclude=%s",
            entity_id,
            ref,
            exclude_bill_id,
        )
        raise BillValidationError(
            "A bill with this invoice number already exists for this entity."
        )


def _final_reference_for_create(entity_id: str, user_id: str, incoming: str | None) -> str:
    """Use explicit reference if provided; otherwise generate MB… + timestamp (unique per entity)."""
    ref = (incoming or "").strip()
    if ref:
        _ensure_reference_unique(entity_id, ref)
        return ref
    user = User.objects.get(id=user_id)
    return generate_unique_bill_reference(entity_id, user)


def _fmt(value) -> str:
    """Return a human-friendly string for an audit field value."""
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _collect_field_changes(bill: Bill, data, fields: tuple[str, ...]) -> list[str]:
    """Compare current bill fields to incoming data, return list of change descriptions."""
    changes: list[str] = []
    for field in fields:
        new_val = getattr(data, field, None)
        if new_val is None:
            continue
        old_val = getattr(bill, field)
        if isinstance(old_val, Decimal):
            new_val = Decimal(str(new_val))
        if str(old_val) != str(new_val):
            label = _FIELD_LABELS.get(field, field.replace("_", " ").title())
            changes.append(f"{label}: {_fmt(old_val)} → {_fmt(new_val)}")
    return changes


def _collect_line_item_changes(bill: Bill, data) -> list[str]:
    """Detect meaningful changes in line items (account code / account name)."""
    if data.line_items is None:
        return []
    old_lines = list(bill.line_items.all().order_by("sort_order"))
    new_lines = list(data.line_items)
    changes: list[str] = []
    for idx, new_li in enumerate(new_lines):
        if idx < len(old_lines):
            old_li = old_lines[idx]
            if (new_li.account_code or "") != (old_li.account_code or ""):
                old_label = f"{old_li.account_code} - {old_li.account_name}".strip(" -")
                new_label = f"{new_li.account_code} - {getattr(new_li, 'account_name', '')}".strip(" -")
                changes.append(f"Account code: {_fmt(old_label)} → {_fmt(new_label)}")
    if len(new_lines) != len(old_lines):
        changes.append(f"Line items: {len(old_lines)} → {len(new_lines)}")
    return changes


def _build_change_detail(changes: list[str], fallback: str = "Bill updated") -> str:
    if not changes:
        return fallback
    return "; ".join(changes)


def validate_for_submission(data, bill=None):
    errors = []
    contact = getattr(data, "contact", None) or (bill.contact if bill else None)
    if not contact:
        errors.append("Contact is required.")

    amount = getattr(data, "amount", None)
    if amount is None and bill:
        amount = bill.amount
    if amount is None or amount <= 0:
        errors.append("Amount must be greater than zero.")

    invoice_date = getattr(data, "invoice_date", None) or (bill.invoice_date if bill else None)
    if not invoice_date:
        errors.append("Invoice date is required.")

    due_date = getattr(data, "due_date", None) or (bill.due_date if bill else None)
    if not due_date:
        errors.append("Due date is required.")

    has_line_items = bool(getattr(data, "line_items", None))
    if not has_line_items and bill:
        has_line_items = bill.line_items.exists()

    has_attachments = False
    if bill:
        has_attachments = bill.bill_attachments.exists()

    if not has_line_items and not has_attachments:
        errors.append("At least one attachment or line item is required.")

    if errors:
        raise BillValidationError("; ".join(errors))


def save_bill_draft(data, user_id: str, entity_id: str) -> Bill:
    ref = _final_reference_for_create(entity_id, user_id, getattr(data, "reference", None))
    with transaction.atomic():
        bill = Bill.objects.create(
            entity_id=entity_id,
            contact=data.contact or "",
            xero_contact_id=data.xero_contact_id or "",
            description=data.description or "",
            amount=data.amount or 0,
            due_date=data.due_date,
            invoice_date=data.invoice_date,
            reference=ref,
            currency_code=data.currency_code or "",
            xero_account_code=getattr(data, "xero_account_code", None) or "",
            uploaded_by=user_id,
            status=Bill.Status.DRAFT,
        )

        for idx, item in enumerate(data.line_items or []):
            BillLineItem.objects.create(
                bill=bill,
                description=item.description,
                quantity=item.quantity,
                unit_amount=item.unit_amount,
                line_amount=item.line_amount,
                account_code=item.account_code,
                account_name=item.account_name,
                tax_type=item.tax_type,
                sort_order=item.sort_order or idx,
                note=item.note,
            )

        log_audit(bill, Audit.Action.CREATED, user_id, "Saved as draft")
        logger.info("Draft bill saved id=%s entity=%s", bill.id, entity_id)
    return bill


def update_bill_draft(bill: Bill, data, user_id: str) -> Bill:
    if bill.status == Bill.Status.VOIDED:
        raise BillValidationError("Cannot edit a voided bill.")
    draft_fields = (
        "contact", "xero_contact_id", "description", "amount",
        "due_date", "invoice_date", "reference", "currency_code",
        "xero_account_code",
    )
    if getattr(data, "reference", None) is not None and not _reference_unchanged_for_bill(
        bill, data.reference
    ):
        _ensure_reference_unique(
            bill.entity_id, data.reference, exclude_bill_id=str(bill.pk)
        )
    changes = _collect_field_changes(bill, data, draft_fields)
    changes.extend(_collect_line_item_changes(bill, data))

    with transaction.atomic():
        for field in draft_fields:
            new_val = getattr(data, field, None)
            if new_val is not None:
                setattr(bill, field, new_val)

        bill.status = Bill.Status.DRAFT

        if data.line_items is not None:
            bill.line_items.all().delete()
            for idx, item in enumerate(data.line_items):
                BillLineItem.objects.create(
                    bill=bill,
                    description=item.description,
                    quantity=item.quantity,
                    unit_amount=item.unit_amount,
                    line_amount=item.line_amount,
                    account_code=item.account_code,
                    account_name=item.account_name,
                    tax_type=item.tax_type,
                    sort_order=item.sort_order or idx,
                    note=item.note,
                )

        bill.save()
        log_audit(bill, Audit.Action.EDITED, user_id,
                  _build_change_detail(changes, "Draft updated"))

    logger.info("Draft bill updated id=%s by user=%s", bill.id, user_id)
    return bill


def create_bill(data, user_id: str, entity_id: str) -> Bill:
    ref = _final_reference_for_create(entity_id, user_id, getattr(data, "reference", None))
    with transaction.atomic():
        bill = Bill.objects.create(
            entity_id=entity_id,
            contact=data.contact,
            xero_contact_id=data.xero_contact_id,
            description=data.description,
            amount=data.amount,
            due_date=data.due_date,
            invoice_date=data.invoice_date,
            reference=ref,
            currency_code=data.currency_code,
            xero_account_code=getattr(data, "xero_account_code", "") or "",
            uploaded_by=user_id,
            status=Bill.Status.DRAFT,
        )

        for idx, item in enumerate(data.line_items):
            BillLineItem.objects.create(
                bill=bill,
                description=item.description,
                quantity=item.quantity,
                unit_amount=item.unit_amount,
                line_amount=item.line_amount,
                account_code=item.account_code,
                account_name=item.account_name,
                tax_type=item.tax_type,
                sort_order=item.sort_order or idx,
                note=item.note,
            )

        log_audit(bill, Audit.Action.CREATED, user_id, "Bill created")
        logger.info("Bill created id=%s entity=%s", bill.id, entity_id)
    return bill


def submit_bill(data, user_id: str, entity_id: str) -> Bill:
    validate_for_submission(data)
    ref = _final_reference_for_create(entity_id, user_id, getattr(data, "reference", None))
    with transaction.atomic():
        bill = Bill.objects.create(
            entity_id=entity_id,
            contact=data.contact,
            xero_contact_id=data.xero_contact_id,
            description=data.description,
            amount=data.amount,
            due_date=data.due_date,
            invoice_date=data.invoice_date,
            reference=ref,
            currency_code=data.currency_code,
            xero_account_code=getattr(data, "xero_account_code", "") or "",
            uploaded_by=user_id,
            status=Bill.Status.SUBMITTED,
        )

        for idx, item in enumerate(data.line_items):
            BillLineItem.objects.create(
                bill=bill,
                description=item.description,
                quantity=item.quantity,
                unit_amount=item.unit_amount,
                line_amount=item.line_amount,
                account_code=item.account_code,
                account_name=item.account_name,
                tax_type=item.tax_type,
                sort_order=item.sort_order or idx,
                note=item.note,
            )

        log_audit(bill, Audit.Action.SUBMITTED, user_id, "Bill created and submitted")
        logger.info("Bill submitted id=%s entity=%s", bill.id, entity_id)
    return bill


def update_bill(bill: Bill, data, user_id: str) -> Bill:
    if bill.status == Bill.Status.VOIDED:
        raise BillValidationError("Cannot edit a voided bill.")
    updatable_fields = (
        "contact", "xero_contact_id", "description", "amount",
        "status", "due_date", "invoice_date", "reference", "currency_code",
        "xero_account_code",
    )
    if getattr(data, "status", None) == "submitted" and bill.status != "submitted":
        validate_for_submission(data, bill=bill)

    if getattr(data, "reference", None) is not None and not _reference_unchanged_for_bill(
        bill, data.reference
    ):
        _ensure_reference_unique(
            bill.entity_id, data.reference, exclude_bill_id=str(bill.pk)
        )

    old_status = bill.status
    changes = _collect_field_changes(bill, data, updatable_fields)
    changes.extend(_collect_line_item_changes(bill, data))

    with transaction.atomic():
        for field in updatable_fields:
            new_val = getattr(data, field, None)
            if new_val is not None:
                setattr(bill, field, new_val)

        if data.line_items is not None:
            bill.line_items.all().delete()
            for idx, item in enumerate(data.line_items):
                BillLineItem.objects.create(
                    bill=bill,
                    description=item.description,
                    quantity=item.quantity,
                    unit_amount=item.unit_amount,
                    line_amount=item.line_amount,
                    account_code=item.account_code,
                    account_name=item.account_name,
                    tax_type=item.tax_type,
                    sort_order=item.sort_order or idx,
                    note=item.note,
                )

        bill.save()
        # Paid / partially_paid must stay aligned with completed payments when
        # amount changes (e.g. paid bill total increased → partially_paid).
        sync_bill_status_from_payments(bill, user_id="")

        new_status = bill.status
        if old_status != new_status:
            _STATUS_ACTION_MAP = {
                "submitted": Audit.Action.SUBMITTED,
                "paid": Audit.Action.MARKED_PAID,
                "voided": Audit.Action.VOIDED,
                "cancelled": Audit.Action.CANCELLED,
            }
            action = _STATUS_ACTION_MAP.get(new_status, Audit.Action.STATUS_CHANGED)
            detail = _build_change_detail(changes,
                                          f"Status changed from {old_status} to {new_status}")
            log_audit(bill, action, user_id, detail)
        else:
            detail = _build_change_detail(changes, "Bill updated")
            log_audit(bill, Audit.Action.EDITED, user_id, detail)

    logger.info("Bill updated id=%s by user=%s", bill.id, user_id)
    return bill


def _hard_delete_draft_bill(bill: Bill, user_id: str) -> None:
    """Remove storage and DB rows for a draft bill (no voided tombstone)."""
    from bills.services.attachment_service import (
        delete_attachment,
        delete_payment_attachment,
    )

    bill_id = bill.id
    logger.info("Hard-deleting draft bill id=%s by user=%s", bill_id, user_id)

    for payment in list(bill.payments.all()):
        for pa_id in list(
            payment.payment_attachments.values_list("id", flat=True),
        ):
            delete_payment_attachment(payment, str(pa_id), user_id)

    for ba_id in list(bill.bill_attachments.values_list("id", flat=True)):
        delete_attachment(bill, str(ba_id), user_id)

    bill.delete()
    logger.info("Draft bill id=%s hard-deleted", bill_id)


def delete_bill(bill: Bill, user_id: str) -> str:
    """Draft bills are hard-deleted; all other deletable statuses become voided."""
    if bill.status == Bill.Status.DRAFT:
        with transaction.atomic():
            _hard_delete_draft_bill(bill, user_id)
        return "deleted"

    with transaction.atomic():
        bill.status = Bill.Status.VOIDED
        bill.save(update_fields=["status", "updated_at"])
        log_audit(bill, Audit.Action.VOIDED, user_id, "Bill voided")
    logger.info("Bill voided id=%s by user=%s", bill.id, user_id)
    return "voided"
