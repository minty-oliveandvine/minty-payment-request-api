import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from bills.models import Audit, Bill, Payment
from bills.services.audit_service import log_audit
from core.exceptions import BillValidationError

logger = logging.getLogger("minty-api")


def _completed_total(bill: Bill, exclude_payment_id: str | None = None) -> Decimal:
    """Sum of completed payment amounts for the bill, optionally excluding one payment."""
    qs = Payment.objects.filter(bill=bill, payment_status="completed")
    if exclude_payment_id:
        qs = qs.exclude(id=exclude_payment_id)
    return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _reserved_total(bill: Bill, exclude_payment_id: str | None = None) -> Decimal:
    """Sum of pending + completed payment amounts (counts toward bill cap), excluding one row if given."""
    qs = Payment.objects.filter(
        bill=bill,
        payment_status__in=(
            Payment.PaymentStatus.PENDING,
            Payment.PaymentStatus.COMPLETED,
        ),
    )
    if exclude_payment_id:
        qs = qs.exclude(id=exclude_payment_id)
    return qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _status_counts_toward_bill_cap(payment_status: str) -> bool:
    return payment_status in (
        Payment.PaymentStatus.PENDING,
        Payment.PaymentStatus.COMPLETED,
    )


def _check_payment_within_bill_total(
    bill: Bill, new_amount: Decimal, exclude_payment_id: str | None = None
):
    """Raise if this payment would exceed the bill total (pending + completed must not sum over bill.amount)."""
    existing = _reserved_total(bill, exclude_payment_id)
    remaining = bill.amount - existing
    if new_amount > remaining:
        raise BillValidationError(
            f"Payment of {new_amount} exceeds remaining balance. "
            f"Bill total: {bill.amount}, already allocated (pending + completed): {existing}, "
            f"remaining: {remaining}"
        )


# Backward-compatible name for the same guard (avoids NameError if any caller still says _check_overpayment).
_check_overpayment = _check_payment_within_bill_total


def sync_bill_status_from_payments(bill: Bill, user_id: str = "") -> None:
    """Recompute paid / partially_paid from completed payments vs ``bill.amount``.

    Used after a bill update (e.g. accountant raised the total on a paid bill) so
    status matches payment totals. Pass ``user_id=""`` when the caller will audit
    the transition (avoids duplicate audit rows).
    """
    _update_bill_status(bill, user_id)


def _update_bill_status(bill: Bill, user_id: str = "") -> None:
    """Set bill status based on completed payments vs bill amount."""
    completed_sum = _completed_total(bill)

    if bill.amount > 0 and completed_sum >= bill.amount:
        new_status = Bill.Status.PAID
    elif completed_sum > 0:
        new_status = Bill.Status.PARTIALLY_PAID
    else:
        return

    if bill.status != new_status:
        old_status = bill.status
        bill.status = new_status
        bill.save(update_fields=["status", "updated_at"])
        if user_id:
            action = Audit.Action.MARKED_PAID if new_status == Bill.Status.PAID else Audit.Action.STATUS_CHANGED
            log_audit(
                bill, action, user_id,
                f"Status changed from {old_status} to {new_status} "
                f"(paid {completed_sum}/{bill.amount})",
            )
        logger.info(
            "Bill %s status %s -> %s (paid=%s/%s)",
            bill.id, old_status, new_status, completed_sum, bill.amount,
        )


def get_amount_due(bill: Bill) -> Decimal:
    """Return the remaining unpaid balance for the bill."""
    paid = _completed_total(bill)
    remaining = bill.amount - paid
    return max(remaining, Decimal("0"))


def create_payment(bill: Bill, data, user_id: str) -> Payment:
    with transaction.atomic():
        if _status_counts_toward_bill_cap(data.payment_status):
            _check_payment_within_bill_total(bill, data.amount)
        payment = Payment.objects.create(
            bill=bill,
            payment_date=data.payment_date,
            amount=data.amount,
            currency_code=data.currency_code,
            payment_method=data.payment_method,
            payment_status=data.payment_status,
            reference_no=data.reference_no,
            note=data.note,
            xero_payment_id=data.xero_payment_id,
            created_by=user_id,
        )
        log_audit(
            bill, Audit.Action.PAYMENT_CREATED, user_id,
            f"Payment of {data.amount} created ({data.payment_method or 'no method'})",
        )
        _update_bill_status(bill, user_id)
    logger.info("Payment created id=%s bill_id=%s", payment.id, bill.id)
    return payment


def update_payment(payment: Payment, data, user_id: str) -> Payment:
    updatable_fields = (
        "payment_date", "amount", "currency_code", "payment_method",
        "payment_status", "reference_no", "note", "xero_payment_id",
    )
    with transaction.atomic():
        new_amount = data.amount if data.amount is not None else payment.amount
        new_status = data.payment_status if data.payment_status is not None else payment.payment_status
        if _status_counts_toward_bill_cap(new_status):
            _check_payment_within_bill_total(
                payment.bill, new_amount, exclude_payment_id=str(payment.id)
            )

        for field in updatable_fields:
            new_val = getattr(data, field, None)
            if new_val is not None:
                setattr(payment, field, new_val)
        payment.save()
        log_audit(
            payment.bill, Audit.Action.PAYMENT_UPDATED, user_id,
            f"Payment {payment.id} updated",
        )
        _update_bill_status(payment.bill, user_id)
    logger.info("Payment updated id=%s by user=%s", payment.id, user_id)
    return payment


def delete_payment(payment: Payment, user_id: str):
    payment_id = payment.id
    amount = payment.amount
    bill = payment.bill
    with transaction.atomic():
        payment.delete()
        log_audit(
            bill, Audit.Action.PAYMENT_DELETED, user_id,
            f"Payment of {amount} deleted",
        )
        _update_bill_status(bill, user_id)
    logger.info("Payment deleted id=%s by user=%s", payment_id, user_id)
