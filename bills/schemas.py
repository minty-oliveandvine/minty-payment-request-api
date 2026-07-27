from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from ninja import Schema

# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------


class MessageOut(Schema):
    message: str


class ErrorOut(Schema):
    detail: str


class SuggestedReferenceOut(Schema):
    reference: str


# ---------------------------------------------------------------------------
# Line Items
# ---------------------------------------------------------------------------


class LineItemIn(Schema):
    description: str = ""
    quantity: Decimal = Decimal("1")
    unit_amount: Decimal = Decimal("0")
    line_amount: Decimal = Decimal("0")
    account_code: str = ""
    account_name: str = ""
    tax_type: str = ""
    sort_order: int = 0
    note: str = ""


class LineItemOut(Schema):
    id: str
    description: str
    quantity: Decimal
    unit_amount: Decimal
    line_amount: Decimal
    account_code: str
    account_name: str
    tax_type: str
    sort_order: int
    note: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


class AttachmentOut(Schema):
    id: str
    original_name: str
    mime_type: str
    file_size: int
    file_extension: str
    storage_provider: str
    created_at: datetime
    download_url: str = ""


class BillAttachmentOut(Schema):
    id: str
    attachment: AttachmentOut
    attachment_role: str
    sort_order: int
    note: str
    created_at: datetime


class PaymentAttachmentOut(Schema):
    id: str
    attachment: AttachmentOut
    attachment_role: str
    sort_order: int
    note: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


class BillCreateIn(Schema):
    contact: str = ""
    xero_contact_id: str = ""
    description: str = ""
    amount: Decimal = Decimal("0")
    due_date: date | None = None
    invoice_date: date | None = None
    reference: str = ""
    currency_code: str = ""
    xero_account_code: str = ""
    line_items: list[LineItemIn] = []


class BillDraftIn(Schema):
    contact: str | None = None
    xero_contact_id: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    due_date: date | None = None
    invoice_date: date | None = None
    reference: str | None = None
    currency_code: str | None = None
    xero_account_code: str | None = None
    line_items: list[LineItemIn] | None = None


class ReturnBillIn(Schema):
    status: str  # "payment_requested" → transition to returned | "returned" → back to submitted | "void" → void


class BillUpdateIn(Schema):
    contact: str | None = None
    xero_contact_id: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    status: str | None = None
    due_date: date | None = None
    invoice_date: date | None = None
    reference: str | None = None
    currency_code: str | None = None
    xero_account_code: str | None = None
    line_items: list[LineItemIn] | None = None


class BillOut(Schema):
    id: str
    entity_id: str
    contact: str
    xero_contact_id: str
    status: str
    amount: Decimal
    amount_due: Decimal
    description: str
    due_date: date | None
    invoice_date: date | None
    reference: str
    currency_code: str
    xero_account_code: str
    published: str
    uploaded_by: str
    created_at: datetime
    updated_at: datetime
    attachments: list[BillAttachmentOut] = []
    line_items: list[LineItemOut] = []


class BillListOut(Schema):
    id: str
    entity_id: str
    contact: str
    status: str
    amount: Decimal
    amount_due: Decimal
    description: str
    due_date: date | None
    invoice_date: date | None
    reference: str
    currency_code: str
    xero_account_code: str
    published: str
    created_at: datetime
    uploaded_by: str
    paid_at: date | None


class BillFilterQuery(Schema):
    status: str | None = None
    contact: str | None = None
    search: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    date_field: str | None = None  # "invoice_date" | "created_at"
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    sort_by: str = "-created_at"
    page: int = 1
    page_size: int = 20


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


class PaymentCreateIn(Schema):
    payment_date: date | None = None
    amount: Decimal = Decimal("0")
    currency_code: str = ""
    payment_method: str = ""
    payment_status: str = "completed"
    reference_no: str = ""
    note: str = ""
    xero_payment_id: str = ""


class PaymentUpdateIn(Schema):
    payment_date: date | None = None
    amount: Decimal | None = None
    currency_code: str | None = None
    payment_method: str | None = None
    payment_status: str | None = None
    reference_no: str | None = None
    note: str | None = None
    xero_payment_id: str | None = None


class PaymentOut(Schema):
    id: str
    bill_id: str
    payment_date: date | None
    amount: Decimal
    currency_code: str
    payment_method: str
    payment_status: str
    reference_no: str
    note: str
    xero_payment_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    attachments: list[PaymentAttachmentOut] = []


class PaymentListOut(Schema):
    id: str
    bill_id: str
    bill_reference: str = ""
    bill_status: str = ""
    payment_date: date | None
    amount: Decimal
    payment_method: str
    payment_status: str
    reference_no: str
    created_by: str
    created_by_name: str = ""
    created_at: datetime


class PaymentListResponse(Schema):
    paid_total: Decimal
    payments: list[PaymentListOut]


class PaymentFilterQuery(Schema):
    payment_status: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    sort_by: str = "-payment_date"
    page: int = 1
    page_size: int = 20


# ---------------------------------------------------------------------------
# Entity Functions
# ---------------------------------------------------------------------------


class EntityFunctionCreateIn(Schema):
    function_code: str
    function_name: str
    description: str = ""
    is_active: bool = True


class EntityFunctionUpdateIn(Schema):
    function_code: str | None = None
    function_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class EntityFunctionOut(Schema):
    id: str
    function_code: str
    function_name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Entity Function Maps
# ---------------------------------------------------------------------------


class EntityFunctionMapCreateIn(Schema):
    entity_function_id: str
    is_enabled: bool = True
    settings_json: dict | None = None


class EntityFunctionMapUpdateIn(Schema):
    is_enabled: bool | None = None
    enabled_at: datetime | None = None
    disabled_at: datetime | None = None
    settings_json: dict | None = None


class EntityFunctionMapOut(Schema):
    id: str
    entity_id: str
    entity_function_id: str
    is_enabled: bool
    enabled_at: datetime | None
    disabled_at: datetime | None
    settings_json: dict | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class EntityFunctionNameOut(Schema):
    function_name: str


# ---------------------------------------------------------------------------
# Entity Bill Account Xero
# ---------------------------------------------------------------------------


class EntityBillAccountXeroCreateIn(Schema):
    account_code: str
    account_name: str = ""
    account_type: str = ""
    is_default: bool = False
    is_active: bool = True
    is_deleted: bool = False
    xero_account_id: str = ""
    sort_order: int = 0


class EntityBillAccountXeroUpdateIn(Schema):
    account_code: str | None = None
    account_name: str | None = None
    account_type: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    is_deleted: bool | None = None
    xero_account_id: str | None = None
    sort_order: int | None = None


class EntityBillAccountXeroOut(Schema):
    id: str
    entity_id: str
    account_code: str
    account_name: str
    account_type: str
    is_default: bool
    is_active: bool
    is_deleted: bool
    xero_account_id: str
    sort_order: int
    created_by: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Entity Bill Contacts (from xero_contact_sync, managed by Flask)
# ---------------------------------------------------------------------------


class EntityBillContactOut(Schema):
    id: str
    entity_id: str
    xero_contact_id: str
    xero_org_id: str | None = None
    name: str
    category: str | None = None


class EntityBillContactCreateIn(Schema):
    name: str


# ---------------------------------------------------------------------------
# Currency Info
# ---------------------------------------------------------------------------


class CurrencyInfoCreateIn(Schema):
    currency_code: str
    currency_name: str
    symbol: str = ""
    decimal_places: int = 2
    is_active: bool = True


class CurrencyInfoUpdateIn(Schema):
    currency_code: str | None = None
    currency_name: str | None = None
    symbol: str | None = None
    decimal_places: int | None = None
    is_active: bool | None = None


class CurrencyInfoOut(Schema):
    id: UUID
    currency_code: str
    currency_name: str
    symbol: str
    decimal_places: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Entity Bill Currency
# ---------------------------------------------------------------------------


class EntityBillCurrencyCreateIn(Schema):
    currency_info_id: str
    is_default: bool = False
    is_enabled: bool = True
    sort_order: int = 0


class EntityBillCurrencyUpdateIn(Schema):
    is_default: bool | None = None
    is_enabled: bool | None = None
    sort_order: int | None = None


class EntityBillCurrencyOut(Schema):
    id: str
    entity_id: str
    currency_info_id: UUID
    is_default: bool
    is_enabled: bool
    sort_order: int
    created_by: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Xero Bill Sync
# ---------------------------------------------------------------------------


class XeroBillSyncLineOut(Schema):
    id: str
    bill_line_item_id: str | None
    description: str
    quantity: Decimal
    unit_amount: Decimal
    line_amount: Decimal
    account_code: str
    tax_type: str
    sort_order: int
    response_line_item_id: str
    response_account_id: str
    response_tax_amount: Decimal | None
    created_at: datetime


class XeroBillSyncPayloadOut(Schema):
    id: str
    request_json: dict | None
    response_json: dict | None
    request_headers: dict | None
    response_headers: dict | None
    created_at: datetime
    updated_at: datetime


class XeroBillResponseLineOut(Schema):
    id: str
    xero_line_item_id: str
    description: str
    quantity: Decimal
    unit_amount: Decimal
    line_amount: Decimal
    tax_type: str
    tax_amount: Decimal
    account_code: str
    account_id: str
    validation_errors: dict | None
    created_at: datetime


class XeroBillSyncOut(Schema):
    id: str
    bill_id: str
    sync_direction: str
    sync_type: str
    sync_status: str
    request_type: str
    request_status: str
    request_contact_id: str
    request_invoice_number: str
    request_reference: str
    request_invoice_date: date | None
    request_due_date: date | None
    response_invoice_id: str
    response_invoice_number: str
    response_status: str
    response_amount_due: Decimal | None
    response_amount_paid: Decimal | None
    response_total: Decimal | None
    response_currency_code: str
    xero_response_id: str
    xero_provider_name: str
    xero_datetime_utc: str
    http_status_code: int | None
    idempotency_key: str
    retry_count: int
    last_retry_at: datetime | None
    has_errors: bool
    error_message: str
    requested_by: str
    requested_at: datetime | None
    responded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sync_lines: list[XeroBillSyncLineOut] = []
    payload: XeroBillSyncPayloadOut | None = None
    response_lines: list[XeroBillResponseLineOut] = []


class XeroBillSyncListOut(Schema):
    id: str
    bill_id: str
    sync_direction: str
    sync_type: str
    sync_status: str
    has_errors: bool
    http_status_code: int | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditOut(Schema):
    id: str
    bill_id: str
    action: str
    detail: str
    date: datetime
    user_id: str
    user_name: str = ""
    user_email: str = ""


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileIn(Schema):
    email: str
    first_name: str
    last_name: str


class ProfileOut(Schema):
    id: str
    email: str
    first_name: str
    last_name: str
    username: str
    is_view_only: bool = False
    member_entity_ids: list[str] = []
