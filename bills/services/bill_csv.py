"""The bills CSV (owner, 2026-11-13): ``POST /bills/export.csv`` - the bills the list shows the
person, one line per bill. Database only.

The list loads every bill and filters in the browser, so the web app sends the ids of what it
shows: the ticked bills, or every bill the filters leave when none are ticked (``ids``, at most
``MAX_IDS`` - the list's own cap). Every id must be one of this company's bills; an id that isn't
refuses the whole request (404), never skipped, so a file never quietly misses a bill.

Columns are the list's words: Payment No. (the reference), the supplier, the dates, the Status
pill's label, the description, the line accounts' codes (``; ``-joined, each once, in line order;
the bill's own code when it has no lines), the amount, the unpaid amount (as ``get_amount_due``:
nothing on a voided bill), the date of the newest completed payment and the currency code. Amounts
have two decimal places. Four queries for any number of bills.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Prefetch, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from bills.models import Bill, BillLine, CurrencyInfo, Payment
from core.exceptions import BillValidationError

COLUMNS = (
    "Payment No.", "Supplier", "Invoice Date", "Due Date", "Submitted Date", "Status", "Description",
    "Account Code", "Amount", "Unpaid Amount", "Paid Date", "Currency",
)
MAX_IDS = 2000
#: The Status pill's labels (minty-payment-request-web ``lib/billStatusDisplay.ts``).
STATUS_LABELS = {
    "draft": "Draft",
    "submitted": "Payment Requested",
    "paid": "Paid",
    "partially_paid": "Partially Paid",
    "void": "Voided",
    "returned": "Returned",
}

NOTHING_CHOSEN = "There are no bills to download. Tick some bills, or change the filters."
TOO_MANY = f"That's more than {MAX_IDS:,} bills. Narrow the filters to {MAX_IDS:,} or fewer."
NOT_FOUND = "One of the bills isn't in this company any more. Refresh the list and try again."

_CENT = Decimal("0.01")


class NothingToExport(LookupError):
    """No bill to write: ``message`` says why (a 404)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def chosen_bills(entity_id, ids) -> tuple[list[Bill], str]:
    """The bills ``ids`` names, in file order (by invoice date), and the file's name. Refuses an
    empty or oversized list (``BillValidationError``, 422) and any id that is not one of this
    company's bills (``NothingToExport``, 404)."""
    wanted = {str(i).strip().lower() for i in ids or []}
    if not wanted:
        raise BillValidationError(NOTHING_CHOSEN)
    if len(wanted) > MAX_IDS:
        raise BillValidationError(TOO_MANY)
    try:
        bills = _with_figures(Bill.objects.filter(entity_id=entity_id, id__in=wanted))
    except (ValidationError, ValueError):  # a malformed id is not one of this company's bills
        raise NothingToExport(NOT_FOUND) from None
    if {str(b.id) for b in bills} != wanted:
        raise NothingToExport(NOT_FOUND)
    return bills, f"bills_{timezone.localdate().isoformat()}.csv"


def _with_figures(qs) -> list[Bill]:
    """The bills with what each row needs, in three more queries at most: their lines, the paid
    total and newest paid date (``list_bills``' rule), and their currencies' codes."""
    completed = Payment.objects.filter(bill=OuterRef("pk"), payment_status=Payment.PaymentStatus.COMPLETED)
    bills = list(
        qs.annotate(
            paid_at=Subquery(completed.order_by("-created_at").values("payment_date")[:1]),
            paid_total=Coalesce(
                Subquery(completed.order_by().values("bill").annotate(t=Sum("amount")).values("t")[:1]),
                Decimal(0),
            ),
        )
        .prefetch_related(Prefetch("line_items", queryset=BillLine.objects.order_by("sort_order", "created_at")))
        .order_by("invoice_date", "created_at", "id")
    )
    currency_ids = {b.currency_id for b in bills if b.currency_id}
    codes = dict(CurrencyInfo.objects.filter(id__in=currency_ids).values_list("id", "currency_code")) \
        if currency_ids else {}
    for bill in bills:
        bill.currency_text = (codes.get(bill.currency_id) or "").strip()
    return bills


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(_CENT) + 0)


def _iso(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "date") and callable(value.date):  # a timestamp: the company's day
        value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value.isoformat()


def _accounts(bill: Bill) -> str:
    codes: list[str] = []
    for line in bill.line_items.all():
        code = (line.account_code or "").strip()
        if code and code not in codes:
            codes.append(code)
    return "; ".join(codes) if codes else (bill.xero_account_code or "").strip()


def _unpaid(bill: Bill) -> Decimal:
    """``payment_service.get_amount_due`` from the annotated total (no query a bill)."""
    if bill.status == Bill.Status.VOID:
        return Decimal("0.00")
    return max(bill.amount - bill.paid_total, Decimal(0))


def to_csv(bills) -> bytes:
    """The file: the header, then one line per bill; UTF-8 without a byte-order mark, LF."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for bill in bills:
        writer.writerow([
            bill.reference or "", bill.contact_name or "", _iso(bill.invoice_date), _iso(bill.due_date),
            _iso(bill.created_at), STATUS_LABELS.get(str(bill.status), str(bill.status or "").title()),
            bill.description or "", _accounts(bill), _money(bill.amount), _money(_unpaid(bill)),
            _iso(bill.paid_at), bill.currency_text,
        ])
    return buffer.getvalue().encode("utf-8")

