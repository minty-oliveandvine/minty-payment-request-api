import uuid

from django.db import models

from shared_models.enums import ModuleCode
from shared_models.fields import CharNField, PgEnumField

# ═══════════════════════════════════════════════════════════════════════════
# BILL
# ═══════════════════════════════════════════════════════════════════════════


class Bill(models.Model):
    """A payment request (``bill``). On the schema since C8:

    * ``status`` is the ``bill_status`` enum - ``void`` is what the code called ``voided``;
      ``authorised`` / ``cancelled`` / ``sync_failed`` were never written and are gone.
    * ``published`` is the ``publish_state`` enum - ``draft`` is what the code called
      ``not_published``.
    * ``contact_name`` (was ``contact``), ``contact_id`` (a ``xero_contact_sync`` row; was the
      Xero ContactID in ``xero_contact_id``), ``currency_id`` (a ``currency_info`` row; was
      ``currency_code``) and ``created_by`` (was ``uploaded_by``). The old names stay as
      properties speaking the old values - the API contract and the Xero payload still use
      the contact's Xero id and the currency's code - resolving through the reference rows.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    #: the old spelling, so ``Bill.Status.VOIDED`` keeps meaning the void state
    Status.VOIDED = Status.VOID

    class PublishStatus(models.TextChoices):
        DRAFT = "draft", "Not Published"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    PublishStatus.NOT_PUBLISHED = PublishStatus.DRAFT

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    entity_id = models.UUIDField(db_index=True)
    contact_name = models.CharField(max_length=100, null=True, blank=True)
    contact_id = models.UUIDField(null=True, blank=True)
    status = PgEnumField("bill_status", choices=Status.choices, default=Status.DRAFT)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.TextField(blank=True, default="")
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    bill_number = models.CharField(max_length=50, null=True, blank=True)
    reference = models.CharField(max_length=255, blank=True, default="")
    currency_id = models.UUIDField(null=True, blank=True)
    published = PgEnumField("publish_state", choices=PublishStatus.choices, default=PublishStatus.DRAFT)
    xero_account_code = models.CharField(max_length=20, blank=True, default="")
    created_by = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "bill"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Bill {self.id} — {self.contact} ({self.status})"

    # ---- the pre-C8 names ---------------------------------------------------------------
    @property
    def contact(self) -> str:
        return self.contact_name or ""

    @contact.setter
    def contact(self, value):
        self.contact_name = (value or "") or None

    @property
    def uploaded_by(self):
        return str(self.created_by) if self.created_by else ""

    @uploaded_by.setter
    def uploaded_by(self, value):
        self.created_by = value or None

    @property
    def xero_contact_id(self) -> str:
        """The Xero ContactID of the linked contact (what the picker and the payload use)."""
        if not self.contact_id:
            return ""
        cached = getattr(self, "_xero_contact_id_cache", None)
        if cached and cached[0] == self.contact_id:
            return cached[1]
        from shared_models.models import XeroContactSync

        row = XeroContactSync.objects.filter(id=self.contact_id).only("xero_contact_id").first()
        value = row.xero_contact_id if row else ""
        self._xero_contact_id_cache = (self.contact_id, value)
        return value

    @xero_contact_id.setter
    def xero_contact_id(self, value):
        """Resolve a Xero ContactID to the company's ``xero_contact_sync`` row.

        Get-or-create: the picker offers synced contacts, but a contact created in Xero
        moments ago may not have been synced yet. The old column kept the raw Xero id
        regardless; a row is what the schema keeps, so one is made (named after the bill's
        ``contact_name``) rather than dropping the contact the person chose.
        """
        value = (value or "").strip()
        if not value:
            self.contact_id = None
            self._xero_contact_id_cache = None
            return
        import uuid as _uuid

        from shared_models.models import Entity, XeroContactSync

        row = (
            XeroContactSync.objects.filter(entity_id=self.entity_id, xero_contact_id=value)
            .only("id").first()
        )
        if row is None and self.entity_id:
            org = Entity.objects.filter(id=self.entity_id).values_list("xero_org_id", flat=True).first()
            row = XeroContactSync.objects.create(
                id=_uuid.uuid4(), entity_id=self.entity_id, xero_contact_id=value,
                xero_org_id=org, name=(self.contact_name or value)[:150], category=None,
            )
        self.contact_id = row.id if row else None
        self._xero_contact_id_cache = (self.contact_id, value) if row else None

    @property
    def currency_code(self) -> str:
        if not self.currency_id:
            return ""
        cached = getattr(self, "_currency_code_cache", None)
        if cached and cached[0] == self.currency_id:
            return cached[1]
        row = CurrencyInfo.objects.filter(id=self.currency_id).only("currency_code").first()
        value = (row.currency_code or "").strip() if row else ""
        self._currency_code_cache = (self.currency_id, value)
        return value

    @currency_code.setter
    def currency_code(self, value):
        value = (value or "").strip().upper()
        if not value:
            self.currency_id = None
            self._currency_code_cache = None
            return
        row = CurrencyInfo.objects.filter(currency_code=value).only("id").first()
        self.currency_id = row.id if row else None
        self._currency_code_cache = (self.currency_id, value) if row else None


# ═══════════════════════════════════════════════════════════════════════════
# BILL LINE ITEM
# ═══════════════════════════════════════════════════════════════════════════


class BillLine(models.Model):
    """One line of a bill (``bill_line``; was ``BillLineItem`` / ``bill_line_item``). The
    old class name stays importable and the relationship keeps its name (``line_items``)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
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
        managed = False
        db_table = "bill_line"
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.description} — {self.line_amount}"


BillLineItem = BillLine


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT
# ═══════════════════════════════════════════════════════════════════════════


class BillAudit(models.Model):
    """Who did what to a bill (``bill_audit``; was ``Audit`` / ``audit``). ``created_at`` was
    ``date`` - kept as a property. ``user_id`` may be NULL (a system action)."""

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="audits",
        on_delete=models.CASCADE,
    )
    action = models.CharField(max_length=100, choices=Action.choices)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    user_id = models.UUIDField(null=True, blank=True)

    @property
    def date(self):
        return self.created_at

    class Meta:
        managed = False
        db_table = "bill_audit"
        ordering = ["created_at"]

    def __str__(self):
        return f"Audit {self.id} — {self.action} on Bill {self.bill_id}"


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT
# ═══════════════════════════════════════════════════════════════════════════


class Payment(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partial"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="payments",
        on_delete=models.CASCADE,
    )
    payment_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency_id = models.UUIDField(null=True, blank=True)
    payment_method = models.CharField(max_length=50, blank=True, default="")
    payment_status = PgEnumField("payment_status", choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    reference_no = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(blank=True, default="")
    xero_payment_id = models.CharField(max_length=36, blank=True, default="")
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def currency_code(self) -> str:
        if not self.currency_id:
            return ""
        row = CurrencyInfo.objects.filter(id=self.currency_id).only("currency_code").first()
        return (row.currency_code or "").strip() if row else ""

    @currency_code.setter
    def currency_code(self, value):
        value = (value or "").strip().upper()
        if not value:
            self.currency_id = None
            return
        row = CurrencyInfo.objects.filter(currency_code=value).only("id").first()
        self.currency_id = row.id if row else None

    class Meta:
        managed = False
        db_table = "payment"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.id} — {self.amount} ({self.payment_status})"


# ═══════════════════════════════════════════════════════════════════════════
# ATTACHMENT
# ═══════════════════════════════════════════════════════════════════════════


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=255)
    file_path = models.TextField()
    mime_type = models.CharField(max_length=100)
    file_size = models.BigIntegerField(default=0)
    file_extension = models.CharField(max_length=20, blank=True, default="")
    storage_provider = models.CharField(max_length=50, default="s3")
    checksum_sha256 = models.CharField(max_length=128, blank=True, default="")
    uploaded_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "attachment"

    def __str__(self):
        return self.original_name


# ═══════════════════════════════════════════════════════════════════════════
# BILL ATTACHMENT (mapping)
# ═══════════════════════════════════════════════════════════════════════════


class BillAttachment(models.Model):
    ATTACHMENT_ROLE_TYPE = "bill_attachment_role"

    class AttachmentRole(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        SUPPORTING_DOCUMENT = "supporting_document", "Supporting Document"
        RECEIPT = "receipt", "Receipt"
        APPROVAL_DOCUMENT = "approval_document", "Approval Document"
        OTHER = "other", "Other"
        PROOF = "proof", "Proof"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
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
    attachment_role = PgEnumField(ATTACHMENT_ROLE_TYPE, choices=AttachmentRole.choices, default=AttachmentRole.OTHER)
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
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "bill_attachment"

    def __str__(self):
        return f"BillAttachment {self.bill_id} -> {self.attachment_id}"


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT ATTACHMENT (mapping)
# ═══════════════════════════════════════════════════════════════════════════


class PaymentAttachment(models.Model):
    ATTACHMENT_ROLE_TYPE = "payment_attachment_role"

    class AttachmentRole(models.TextChoices):
        BANK_SLIP = "bank_slip", "Bank Slip"
        REMITTANCE_PROOF = "remittance_proof", "Remittance Proof"
        PAYMENT_RECEIPT = "payment_receipt", "Payment Receipt"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
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
    attachment_role = PgEnumField(ATTACHMENT_ROLE_TYPE, choices=AttachmentRole.choices, default=AttachmentRole.OTHER)
    sort_order = models.IntegerField(default=0)
    note = models.TextField(blank=True, default="")
    # Xero Files API tracking — populated after a successful bank-slip upload
    # to the Xero invoice.  Used by upload_bankslip_to_xero() to delete the
    # previously uploaded copy on the next republish so the same file is not
    # added on top multiple times.
    xero_attachment_id = models.CharField(max_length=36, blank=True, default="")
    xero_filename = models.CharField(max_length=255, blank=True, default="")
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "payment_attachment"

    def __str__(self):
        return f"PaymentAttachment {self.payment_id} -> {self.attachment_id}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════


class EntityFunction(models.Model):
    """The module catalogue (Minty seeds it). ``function_code`` is the ``module_code`` enum."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    function_code = PgEnumField("module_code", choices=ModuleCode.choices, unique=True)
    function_name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=999)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "entity_function"

    def __str__(self):
        return self.function_name


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY FUNCTION MAP
# ═══════════════════════════════════════════════════════════════════════════


class EntityFunctionMap(models.Model):
    """Which modules a company has on. Keyed by (entity_id, entity_function_id) - no
    surrogate id (schema). ``created_by`` is the person who first wrote the row, or NULL."""

    pk = models.CompositePrimaryKey("entity_id", "entity_function_id")
    entity_id = models.UUIDField(db_index=True)
    entity_function = models.ForeignKey(
        EntityFunction,
        related_name="entity_mappings",
        on_delete=models.CASCADE,
    )
    is_enabled = models.BooleanField(default=True)
    enabled_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    settings_json = models.JSONField(null=True, blank=True)
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "entity_function_map"

    def __str__(self):
        return f"EntityFunctionMap {self.entity_id} -> {self.entity_function_id}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY BILL ACCOUNT XERO
# ═══════════════════════════════════════════════════════════════════════════


class EntityBillAccountXero(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    entity_id = models.UUIDField(db_index=True)
    account_code = models.CharField(max_length=150)
    account_name = models.CharField(max_length=150, blank=True, default="")
    account_type = models.CharField(max_length=50, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    xero_account_id = models.CharField(max_length=36, blank=True, default="")
    sort_order = models.IntegerField(default=0)
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "entity_bill_account_xero"

    def __str__(self):
        return f"{self.account_code} — {self.account_name}"


# ═══════════════════════════════════════════════════════════════════════════
# CURRENCY INFO
# ═══════════════════════════════════════════════════════════════════════════


class CurrencyInfo(models.Model):
    # The pettycashv3.currency_info PK is a real uuid column (Alembic
    # c8e0a2b4d6f8) — UUIDField so the ORM round-trips it cleanly.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    currency_code = CharNField(max_length=3, unique=True)
    currency_name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, blank=True, default="")
    decimal_places = models.SmallIntegerField(default=2)
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    entity_id = models.UUIDField(db_index=True)
    currency_info = models.ForeignKey(
        CurrencyInfo,
        related_name="entity_currencies",
        on_delete=models.CASCADE,
        db_column="currency_id",  # the schema's column; the attribute keeps its name
    )
    is_default = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "entity_bill_currency"

    def __str__(self):
        return f"EntityBillCurrency {self.entity_id} -> {self.currency_info_id}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSync(models.Model):
    class SyncDirection(models.TextChoices):
        PUSH = "push", "Push"
        PULL = "pull", "Pull"

    SyncDirection.OUTBOUND = SyncDirection.PUSH
    SyncDirection.INBOUND = SyncDirection.PULL

    class SyncType(models.TextChoices):
        CREATE_INVOICE = "create_invoice", "Create Invoice"
        UPDATE_INVOICE = "update_invoice", "Update Invoice"
        VOID_INVOICE = "void_invoice", "Void Invoice"
        FETCH_INVOICE = "fetch_invoice", "Fetch Invoice"

    class SyncStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    bill = models.ForeignKey(
        Bill,
        related_name="xero_syncs",
        on_delete=models.CASCADE,
    )
    sync_direction = PgEnumField("sync_direction", choices=SyncDirection.choices)
    sync_type = models.CharField(max_length=30, choices=SyncType.choices)
    sync_status = PgEnumField("sync_status", choices=SyncStatus.choices, default=SyncStatus.PENDING)
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
    requested_by = models.UUIDField(null=True, blank=True)
    requested_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "xero_bill_sync"
        ordering = ["-created_at"]

    def __str__(self):
        return f"XeroBillSync {self.id} — {self.sync_type} ({self.sync_status})"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC LINE
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSyncLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    xero_bill_sync = models.ForeignKey(
        XeroBillSync,
        related_name="sync_lines",
        on_delete=models.CASCADE,
    )
    bill_line = models.ForeignKey(
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
        managed = False
        db_table = "xero_bill_sync_line"
        ordering = ["sort_order"]

    def __str__(self):
        return f"SyncLine {self.id} — {self.description}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL SYNC PAYLOAD (1:1 with xero_bill_sync)
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillSyncPayload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
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
        managed = False
        db_table = "xero_bill_sync_payload"

    def __str__(self):
        return f"Payload for sync {self.xero_bill_sync_id}"


# ═══════════════════════════════════════════════════════════════════════════
# XERO BILL RESPONSE LINE
# ═══════════════════════════════════════════════════════════════════════════


class XeroBillResponseLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
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
        managed = False
        db_table = "xero_bill_response_line"

    def __str__(self):
        return f"ResponseLine {self.id} — {self.description}"


Audit = BillAudit
