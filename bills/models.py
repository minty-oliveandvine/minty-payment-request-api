import uuid

from django.db import models

# ═══════════════════════════════════════════════════════════════════════════
# BILL
# ═══════════════════════════════════════════════════════════════════════════


class Bill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned"
        AUTHORISED = "authorised", "Authorised"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        VOIDED = "voided", "Voided"
        CANCELLED = "cancelled", "Cancelled"
        SYNC_FAILED = "sync_failed", "Sync Failed"

    class PublishStatus(models.TextChoices):
        NOT_PUBLISHED = "not_published", "Not Published"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    entity_id = models.CharField(max_length=36, db_index=True)
    contact = models.CharField(max_length=100, blank=True, default="")
    xero_contact_id = models.CharField(max_length=36, blank=True, default="")
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.TextField(blank=True, default="")
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=255, blank=True, default="")
    currency_code = models.CharField(max_length=10, blank=True, default="")
    published = models.CharField(
        max_length=30,
        choices=PublishStatus.choices,
        default=PublishStatus.NOT_PUBLISHED,
    )
    xero_account_code = models.CharField(max_length=20, blank=True, default="")
    uploaded_by = models.CharField(max_length=36, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bill"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Bill {self.id} — {self.contact} ({self.status})"


# ═══════════════════════════════════════════════════════════════════════════
# BILL LINE ITEM
# ═══════════════════════════════════════════════════════════════════════════


class BillLineItem(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="line_items",
        on_delete=models.CASCADE,
    )
    description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    unit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    account_code = models.CharField(max_length=20, blank=True, default="")
    account_name = models.CharField(max_length=150, blank=True, default="")
    tax_type = models.CharField(max_length=30, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bill_line_item"
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.description} — {self.line_amount}"


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT
# ═══════════════════════════════════════════════════════════════════════════


class Audit(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        EDITED = "edited", "Edited"
        SUBMITTED = "submitted", "Submitted"
        MARKED_PAID = "marked_paid", "Marked Paid"
        VOIDED = "voided", "Voided"
        CANCELLED = "cancelled", "Cancelled"
        STATUS_CHANGED = "status_changed", "Status Changed"
        PUBLISHED_TO_XERO = "published_to_xero", "Published to Xero"
        ATTACHMENT_UPLOADED = "attachment_uploaded", "Attachment Uploaded"
        ATTACHMENT_DELETED = "attachment_deleted", "Attachment Deleted"
        PAYMENT_CREATED = "payment_created", "Payment Created"
        PAYMENT_UPDATED = "payment_updated", "Payment Updated"
        PAYMENT_DELETED = "payment_deleted", "Payment Deleted"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="audits",
        on_delete=models.CASCADE,
    )
    action = models.CharField(max_length=100, choices=Action.choices)
    detail = models.TextField(blank=True, default="")
    date = models.DateTimeField(auto_now_add=True)
    user_id = models.CharField(max_length=36)

    class Meta:
        db_table = "audit"
        ordering = ["date"]
        indexes = [
            models.Index(fields=["bill"], name="idx_audit_bill_id"),
            models.Index(fields=["date"], name="idx_audit_date"),
        ]

    def __str__(self):
        return f"Audit {self.id} — {self.action} on Bill {self.bill_id}"


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT
# ═══════════════════════════════════════════════════════════════════════════


class Payment(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="payments",
        on_delete=models.CASCADE,
    )
    payment_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=10, blank=True, default="")
    payment_method = models.CharField(max_length=50, blank=True, default="")
    payment_status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    reference_no = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(blank=True, default="")
    xero_payment_id = models.CharField(max_length=36, blank=True, default="")
    created_by = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.id} — {self.amount} ({self.payment_status})"


# ═══════════════════════════════════════════════════════════════════════════
# ATTACHMENT
# ═══════════════════════════════════════════════════════════════════════════


class Attachment(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=255)
    file_path = models.TextField()
    mime_type = models.CharField(max_length=100)
    file_size = models.BigIntegerField(default=0)
    file_extension = models.CharField(max_length=20, blank=True, default="")
    storage_provider = models.CharField(max_length=50, default="s3")
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    uploaded_by = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attachment"

    def __str__(self):
        return self.original_name


# ═══════════════════════════════════════════════════════════════════════════
# BILL ATTACHMENT (mapping)
# ═══════════════════════════════════════════════════════════════════════════


class BillAttachment(models.Model):
    class AttachmentRole(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        SUPPORTING_DOCUMENT = "supporting_document", "Supporting Document"
        RECEIPT = "receipt", "Receipt"
        APPROVAL_DOCUMENT = "approval_document", "Approval Document"
        OTHER = "other", "Other"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="bill_attachments",
        on_delete=models.CASCADE,
    )
    attachment = models.ForeignKey(
        Attachment,
        related_name="bill_attachments",
        on_delete=models.CASCADE,
    )
    attachment_role = models.CharField(
        max_length=50,
        choices=AttachmentRole.choices,
        default=AttachmentRole.OTHER,
    )
    sort_order = models.IntegerField(default=0)
    note = models.TextField(blank=True, default="")
    xero_attachment_id = models.CharField(
        max_length=36,
        blank=True,
        default="",
        help_text="Xero FileId returned after uploading to the Xero Files API.",
    )
    xero_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Slot-based filename used when the file was uploaded to Xero.",
    )
    created_by = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bill_attachment"

    def __str__(self):
        return f"BillAttachment {self.bill_id} -> {self.attachment_id}"


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT ATTACHMENT (mapping)
# ═══════════════════════════════════════════════════════════════════════════


class PaymentAttachment(models.Model):
    class AttachmentRole(models.TextChoices):
        BANK_SLIP = "bank_slip", "Bank Slip"
        REMITTANCE_PROOF = "remittance_proof", "Remittance Proof"
        PAYMENT_RECEIPT = "payment_receipt", "Payment Receipt"
        OTHER = "other", "Other"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    payment = models.ForeignKey(
        Payment,
        related_name="payment_attachments",
        on_delete=models.CASCADE,
    )
    attachment = models.ForeignKey(
        Attachment,
        related_name="payment_attachments",
        on_delete=models.CASCADE,
    )
    attachment_role = models.CharField(
        max_length=50,
        choices=AttachmentRole.choices,
        default=AttachmentRole.OTHER,
    )
    sort_order = models.IntegerField(default=0)
    note = models.TextField(blank=True, default="")
    # Xero Files API tracking — populated after a successful bank-slip upload
    # to the Xero invoice.  Used by upload_bankslip_to_xero() to delete the
    # previously uploaded copy on the next republish so the same file is not
    # added on top multiple times.
    xero_attachment_id = models.CharField(max_length=36, blank=True, default="")
    xero_filename = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_attachment"

    def __str__(self):
        return f"PaymentAttachment {self.payment_id} -> {self.attachment_id}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════


class EntityFunction(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    function_code = models.CharField(max_length=100, unique=True)
    function_name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entity_function"

    def __str__(self):
        return self.function_name


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTION MAP
# ═══════════════════════════════════════════════════════════════════════════


class EntityFunctionMap(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    entity_id = models.CharField(max_length=36, db_index=True)
    entity_function = models.ForeignKey(
        EntityFunction,
        related_name="entity_mappings",
        on_delete=models.CASCADE,
    )
    is_enabled = models.BooleanField(default=True)
    enabled_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    settings_json = models.JSONField(null=True, blank=True)
    created_by = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entity_function_map"

    def __str__(self):
        return f"EntityFunctionMap {self.entity_id} -> {self.entity_function_id}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL ACCOUNT XERO
# ═══════════════════════════════════════════════════════════════════════════


class EntityBillAccountXero(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    entity_id = models.CharField(max_length=36, db_index=True)
    account_code = models.CharField(max_length=150)
    account_name = models.CharField(max_length=150, blank=True, default="")
    account_type = models.CharField(max_length=50, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    xero_account_id = models.CharField(max_length=36, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    created_by = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entity_bill_account_xero"

    def __str__(self):
        return f"{self.account_code} — {self.account_name}"


# ═══════════════════════════════════════════════════════════════════════════
# CURRENCY INFO
# ═══════════════════════════════════════════════════════════════════════════


class CurrencyInfo(models.Model):
    # The pettycashv2.currency_info PK is a real uuid column (Alembic
    # c8e0a2b4d6f8) — UUIDField so the ORM round-trips it cleanly.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    currency_code = models.CharField(max_length=10, unique=True)
    currency_name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, blank=True, default="")
    decimal_places = models.IntegerField(default=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "currency_info"

    def __str__(self):
        return f"{self.currency_code} — {self.currency_name}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL CURRENCY
# ═══════════════════════════════════════════════════════════════════════════


class EntityBillCurrency(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    entity_id = models.CharField(max_length=36, db_index=True)
    currency_info = models.ForeignKey(
        CurrencyInfo,
        related_name="entity_currencies",
        on_delete=models.CASCADE,
    )
    is_default = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_by = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entity_bill_currency"

    def __str__(self):
        return f"EntityBillCurrency {self.entity_id} -> {self.currency_info_id}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSync(models.Model):
    class SyncDirection(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        INBOUND = "inbound", "Inbound"

    class SyncType(models.TextChoices):
        CREATE_INVOICE = "create_invoice", "Create Invoice"
        UPDATE_INVOICE = "update_invoice", "Update Invoice"
        VOID_INVOICE = "void_invoice", "Void Invoice"
        FETCH_INVOICE = "fetch_invoice", "Fetch Invoice"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="xero_syncs",
        on_delete=models.CASCADE,
    )
    sync_direction = models.CharField(
        max_length=20,
        choices=SyncDirection.choices,
    )
    sync_type = models.CharField(max_length=30, choices=SyncType.choices)
    sync_status = models.CharField(
        max_length=30,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
    )
    request_type = models.CharField(max_length=20, blank=True, default="")
    request_status = models.CharField(max_length=30, blank=True, default="")
    request_contact_id = models.CharField(max_length=36, blank=True, default="")
    request_invoice_number = models.CharField(max_length=100, blank=True, default="")
    request_reference = models.CharField(max_length=255, blank=True, default="")
    request_invoice_date = models.DateField(null=True, blank=True)
    request_due_date = models.DateField(null=True, blank=True)
    response_invoice_id = models.CharField(max_length=36, blank=True, default="")
    response_invoice_number = models.CharField(max_length=100, blank=True, default="")
    response_status = models.CharField(max_length=30, blank=True, default="")
    response_amount_due = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    response_amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    response_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    response_currency_code = models.CharField(max_length=10, blank=True, default="")
    xero_response_id = models.CharField(max_length=36, blank=True, default="")
    xero_provider_name = models.CharField(max_length=100, blank=True, default="")
    xero_datetime_utc = models.CharField(max_length=100, blank=True, default="")
    http_status_code = models.IntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=100, blank=True, default="")
    retry_count = models.IntegerField(default=0)
    last_retry_at = models.DateTimeField(null=True, blank=True)
    has_errors = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    requested_by = models.CharField(max_length=36, blank=True, default="")
    requested_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "xero_bill_sync"
        ordering = ["-created_at"]

    def __str__(self):
        return f"XeroBillSync {self.id} — {self.sync_type} ({self.sync_status})"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC LINE
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSyncLine(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    xero_bill_sync = models.ForeignKey(
        XeroBillSync,
        related_name="sync_lines",
        on_delete=models.CASCADE,
    )
    bill_line_item = models.ForeignKey(
        BillLineItem,
        related_name="sync_snapshots",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    unit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    account_code = models.CharField(max_length=20, blank=True, default="")
    tax_type = models.CharField(max_length=30, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    response_line_item_id = models.CharField(max_length=36, blank=True, default="")
    response_account_id = models.CharField(max_length=36, blank=True, default="")
    response_tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "xero_bill_sync_line"
        ordering = ["sort_order"]

    def __str__(self):
        return f"SyncLine {self.id} — {self.description}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC PAYLOAD (1:1 with xero_bill_sync)
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSyncPayload(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    xero_bill_sync = models.OneToOneField(
        XeroBillSync,
        related_name="payload",
        on_delete=models.CASCADE,
    )
    request_json = models.JSONField(null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    request_headers = models.JSONField(null=True, blank=True)
    response_headers = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "xero_bill_sync_payload"

    def __str__(self):
        return f"Payload for sync {self.xero_bill_sync_id}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL RESPONSE LINE
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillResponseLine(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=uuid.uuid4)
    xero_bill_sync = models.ForeignKey(
        XeroBillSync,
        related_name="response_lines",
        on_delete=models.CASCADE,
    )
    xero_line_item_id = models.CharField(max_length=36, blank=True, default="")
    description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    unit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_type = models.CharField(max_length=30, blank=True, default="")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    account_code = models.CharField(max_length=20, blank=True, default="")
    account_id = models.CharField(max_length=36, blank=True, default="")
    validation_errors = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "xero_bill_response_line"

    def __str__(self):
        return f"ResponseLine {self.id} — {self.description}"
