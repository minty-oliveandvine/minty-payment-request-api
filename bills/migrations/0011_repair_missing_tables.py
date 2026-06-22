"""Repair migration: re-create the 11 bills-app tables that exist in
Django's migration state but are physically missing from the STAGING
database.

Tables created (idempotently — CREATE TABLE IF NOT EXISTS):

    attachment, bill_attachment, entity_bill_currency, entity_function,
    entity_function_map, payment, payment_attachment,
    xero_bill_response_line, xero_bill_sync, xero_bill_sync_line,
    xero_bill_sync_payload

Plus bill_line_item as a guard (referenced by a SET NULL FK on
xero_bill_sync_line — added if missing, no-op if present).

Schemas reflect the final state defined by migrations 0001 + 0007 + 0008
+ 0010 (FK ON DELETE rules and Xero attachment columns are baked in).

Apply on STAGING:

    # 1) Mark 0010 applied — it would otherwise fail (its target table
    #    payment_attachment is physically missing on STAGING).
    python manage.py migrate --fake bills 0010

    # 2) Apply this migration.
    python manage.py migrate bills

On environments where the tables already exist (e.g. PRESTAGING,
PRODUCTION), this migration is a no-op.

Caveat: The FK from entity_bill_currency.currency_info_id to
currency_info(id) is intentionally omitted. STAGING's currency_info has
the legacy Alembic schema (PK = currency_id, no `id` column). Add the FK
manually once currency_info is migrated:

    ALTER TABLE pettycashv2.entity_bill_currency
        ADD CONSTRAINT fk_entity_bill_currency_currency_info_id
        FOREIGN KEY (currency_info_id)
        REFERENCES pettycashv2.currency_info(id)
        ON DELETE CASCADE;
"""
from django.db import migrations


REPAIR_SQL = r"""
-- ---------------------------------------------------------------------
-- 1) attachment (already exists on STAGING; included for completeness)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.attachment (
    id                  varchar(36)  NOT NULL PRIMARY KEY,
    original_name       varchar(255) NOT NULL,
    stored_name         varchar(255) NOT NULL,
    file_path           text         NOT NULL,
    mime_type           varchar(100) NOT NULL,
    file_size           bigint       NOT NULL DEFAULT 0,
    file_extension      varchar(20)  NOT NULL DEFAULT '',
    storage_provider    varchar(50)  NOT NULL DEFAULT 's3',
    checksum_sha256     varchar(128) NOT NULL DEFAULT '',
    uploaded_by         varchar(36)  NOT NULL,
    created_at          timestamptz  NOT NULL,
    updated_at          timestamptz  NOT NULL,
    is_deleted          boolean      NOT NULL DEFAULT false,
    deleted_at          timestamptz  NULL
);

-- ---------------------------------------------------------------------
-- 2) entity_function (no FKs; parent of entity_function_map)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.entity_function (
    id              varchar(36)  NOT NULL PRIMARY KEY,
    function_code   varchar(100) NOT NULL UNIQUE,
    function_name   varchar(150) NOT NULL,
    description     text         NOT NULL DEFAULT '',
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL,
    updated_at      timestamptz  NOT NULL
);

-- ---------------------------------------------------------------------
-- 3) entity_function_map -> entity_function (CASCADE)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.entity_function_map (
    id                  varchar(36) NOT NULL PRIMARY KEY,
    entity_id           varchar(36) NOT NULL,
    is_enabled          boolean     NOT NULL DEFAULT true,
    enabled_at          timestamptz NULL,
    disabled_at         timestamptz NULL,
    settings_json       jsonb       NULL,
    created_by          varchar(36) NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL,
    updated_at          timestamptz NOT NULL,
    entity_function_id  varchar(36) NOT NULL,
    CONSTRAINT fk_entity_function_map_entity_function_id
        FOREIGN KEY (entity_function_id)
        REFERENCES pettycashv2.entity_function(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS entity_function_map_entity_id_idx
    ON pettycashv2.entity_function_map(entity_id);
CREATE INDEX IF NOT EXISTS entity_function_map_entity_function_id_idx
    ON pettycashv2.entity_function_map(entity_function_id);

-- ---------------------------------------------------------------------
-- 4) entity_bill_currency  (FK to currency_info OMITTED — see header)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.entity_bill_currency (
    id                varchar(36) NOT NULL PRIMARY KEY,
    entity_id         varchar(36) NOT NULL,
    is_default        boolean     NOT NULL DEFAULT false,
    is_enabled        boolean     NOT NULL DEFAULT true,
    sort_order        integer     NOT NULL DEFAULT 0,
    created_by        varchar(36) NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL,
    updated_at        timestamptz NOT NULL,
    currency_info_id  varchar(36) NOT NULL
);
CREATE INDEX IF NOT EXISTS entity_bill_currency_entity_id_idx
    ON pettycashv2.entity_bill_currency(entity_id);
CREATE INDEX IF NOT EXISTS entity_bill_currency_currency_info_id_idx
    ON pettycashv2.entity_bill_currency(currency_info_id);

-- ---------------------------------------------------------------------
-- 5) bill_line_item -> bill (CASCADE) — guard, may already exist
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.bill_line_item (
    id            varchar(36)    NOT NULL PRIMARY KEY,
    description   text           NOT NULL DEFAULT '',
    quantity      numeric(12, 4) NOT NULL DEFAULT 1,
    unit_amount   numeric(12, 2) NOT NULL DEFAULT 0,
    line_amount   numeric(12, 2) NOT NULL DEFAULT 0,
    account_code  varchar(20)    NOT NULL DEFAULT '',
    account_name  varchar(150)   NOT NULL DEFAULT '',
    tax_type      varchar(30)    NOT NULL DEFAULT '',
    sort_order    integer        NOT NULL DEFAULT 0,
    note          text           NOT NULL DEFAULT '',
    created_at    timestamptz    NOT NULL,
    updated_at    timestamptz    NOT NULL,
    bill_id       varchar(36)    NOT NULL,
    CONSTRAINT fk_bill_line_item_bill_id
        FOREIGN KEY (bill_id)
        REFERENCES pettycashv2.bill(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS bill_line_item_bill_id_idx
    ON pettycashv2.bill_line_item(bill_id);

-- ---------------------------------------------------------------------
-- 6) payment -> bill (CASCADE)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.payment (
    id              varchar(36)    NOT NULL PRIMARY KEY,
    payment_date    date           NULL,
    amount          numeric(12, 2) NOT NULL DEFAULT 0,
    currency_code   varchar(10)    NOT NULL DEFAULT '',
    payment_method  varchar(50)    NOT NULL DEFAULT '',
    payment_status  varchar(30)    NOT NULL DEFAULT 'pending',
    reference_no    varchar(100)   NOT NULL DEFAULT '',
    note            text           NOT NULL DEFAULT '',
    xero_payment_id varchar(36)    NOT NULL DEFAULT '',
    created_by      varchar(36)    NOT NULL,
    created_at      timestamptz    NOT NULL,
    updated_at      timestamptz    NOT NULL,
    bill_id         varchar(36)    NOT NULL,
    CONSTRAINT fk_payment_bill_id
        FOREIGN KEY (bill_id)
        REFERENCES pettycashv2.bill(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS payment_bill_id_idx
    ON pettycashv2.payment(bill_id);

-- ---------------------------------------------------------------------
-- 7) bill_attachment -> bill, attachment (both CASCADE) + 0008 columns
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.bill_attachment (
    id                  varchar(36)  NOT NULL PRIMARY KEY,
    attachment_role     varchar(50)  NOT NULL DEFAULT 'other',
    sort_order          integer      NOT NULL DEFAULT 0,
    note                text         NOT NULL DEFAULT '',
    created_by          varchar(36)  NOT NULL,
    created_at          timestamptz  NOT NULL,
    updated_at          timestamptz  NOT NULL,
    xero_attachment_id  varchar(36)  NOT NULL DEFAULT '',
    xero_filename       varchar(255) NOT NULL DEFAULT '',
    bill_id             varchar(36)  NOT NULL,
    attachment_id       varchar(36)  NOT NULL,
    CONSTRAINT fk_bill_attachment_bill_id
        FOREIGN KEY (bill_id)
        REFERENCES pettycashv2.bill(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_bill_attachment_attachment_id
        FOREIGN KEY (attachment_id)
        REFERENCES pettycashv2.attachment(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS bill_attachment_bill_id_idx
    ON pettycashv2.bill_attachment(bill_id);
CREATE INDEX IF NOT EXISTS bill_attachment_attachment_id_idx
    ON pettycashv2.bill_attachment(attachment_id);

-- ---------------------------------------------------------------------
-- 8) payment_attachment -> payment, attachment (CASCADE) + 0010 columns
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.payment_attachment (
    id                  varchar(36)  NOT NULL PRIMARY KEY,
    attachment_role     varchar(50)  NOT NULL DEFAULT 'other',
    sort_order          integer      NOT NULL DEFAULT 0,
    note                text         NOT NULL DEFAULT '',
    created_by          varchar(36)  NOT NULL,
    created_at          timestamptz  NOT NULL,
    updated_at          timestamptz  NOT NULL,
    xero_attachment_id  varchar(36)  NOT NULL DEFAULT '',
    xero_filename       varchar(255) NOT NULL DEFAULT '',
    payment_id          varchar(36)  NOT NULL,
    attachment_id       varchar(36)  NOT NULL,
    CONSTRAINT fk_payment_attachment_payment_id
        FOREIGN KEY (payment_id)
        REFERENCES pettycashv2.payment(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_payment_attachment_attachment_id
        FOREIGN KEY (attachment_id)
        REFERENCES pettycashv2.attachment(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS payment_attachment_payment_id_idx
    ON pettycashv2.payment_attachment(payment_id);
CREATE INDEX IF NOT EXISTS payment_attachment_attachment_id_idx
    ON pettycashv2.payment_attachment(attachment_id);

-- ---------------------------------------------------------------------
-- 9) xero_bill_sync -> bill (CASCADE)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.xero_bill_sync (
    id                       varchar(36)    NOT NULL PRIMARY KEY,
    sync_direction           varchar(20)    NOT NULL,
    sync_type                varchar(30)    NOT NULL,
    sync_status              varchar(30)    NOT NULL DEFAULT 'pending',
    request_type             varchar(20)    NOT NULL DEFAULT '',
    request_status           varchar(30)    NOT NULL DEFAULT '',
    request_contact_id       varchar(36)    NOT NULL DEFAULT '',
    request_invoice_number   varchar(100)   NOT NULL DEFAULT '',
    request_reference        varchar(255)   NOT NULL DEFAULT '',
    request_invoice_date     date           NULL,
    request_due_date         date           NULL,
    response_invoice_id      varchar(36)    NOT NULL DEFAULT '',
    response_invoice_number  varchar(100)   NOT NULL DEFAULT '',
    response_status          varchar(30)    NOT NULL DEFAULT '',
    response_amount_due      numeric(12, 2) NULL,
    response_amount_paid     numeric(12, 2) NULL,
    response_total           numeric(12, 2) NULL,
    response_currency_code   varchar(10)    NOT NULL DEFAULT '',
    xero_response_id         varchar(36)    NOT NULL DEFAULT '',
    xero_provider_name       varchar(100)   NOT NULL DEFAULT '',
    xero_datetime_utc        varchar(100)   NOT NULL DEFAULT '',
    http_status_code         integer        NULL,
    idempotency_key          varchar(100)   NOT NULL DEFAULT '',
    retry_count              integer        NOT NULL DEFAULT 0,
    last_retry_at            timestamptz    NULL,
    has_errors               boolean        NOT NULL DEFAULT false,
    error_message            text           NOT NULL DEFAULT '',
    requested_by             varchar(36)    NOT NULL DEFAULT '',
    requested_at             timestamptz    NULL,
    responded_at             timestamptz    NULL,
    created_at               timestamptz    NOT NULL,
    updated_at               timestamptz    NOT NULL,
    bill_id                  varchar(36)    NOT NULL,
    CONSTRAINT fk_xero_bill_sync_bill_id
        FOREIGN KEY (bill_id)
        REFERENCES pettycashv2.bill(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS xero_bill_sync_bill_id_idx
    ON pettycashv2.xero_bill_sync(bill_id);

-- ---------------------------------------------------------------------
-- 10) xero_bill_response_line -> xero_bill_sync (CASCADE)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.xero_bill_response_line (
    id                  varchar(36)    NOT NULL PRIMARY KEY,
    xero_line_item_id   varchar(36)    NOT NULL DEFAULT '',
    description         text           NOT NULL DEFAULT '',
    quantity            numeric(12, 4) NOT NULL DEFAULT 0,
    unit_amount         numeric(12, 2) NOT NULL DEFAULT 0,
    line_amount         numeric(12, 2) NOT NULL DEFAULT 0,
    tax_type            varchar(30)    NOT NULL DEFAULT '',
    tax_amount          numeric(12, 2) NOT NULL DEFAULT 0,
    account_code        varchar(20)    NOT NULL DEFAULT '',
    account_id          varchar(36)    NOT NULL DEFAULT '',
    validation_errors   jsonb          NULL,
    created_at          timestamptz    NOT NULL,
    xero_bill_sync_id   varchar(36)    NOT NULL,
    CONSTRAINT fk_xero_bill_response_line_xero_bill_sync_id
        FOREIGN KEY (xero_bill_sync_id)
        REFERENCES pettycashv2.xero_bill_sync(id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS xero_bill_response_line_xero_bill_sync_id_idx
    ON pettycashv2.xero_bill_response_line(xero_bill_sync_id);

-- ---------------------------------------------------------------------
-- 11) xero_bill_sync_line -> xero_bill_sync (CASCADE), bill_line_item (SET NULL)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.xero_bill_sync_line (
    id                       varchar(36)    NOT NULL PRIMARY KEY,
    description              text           NOT NULL DEFAULT '',
    quantity                 numeric(12, 4) NOT NULL DEFAULT 0,
    unit_amount              numeric(12, 2) NOT NULL DEFAULT 0,
    line_amount              numeric(12, 2) NOT NULL DEFAULT 0,
    account_code             varchar(20)    NOT NULL DEFAULT '',
    tax_type                 varchar(30)    NOT NULL DEFAULT '',
    sort_order               integer        NOT NULL DEFAULT 0,
    response_line_item_id    varchar(36)    NOT NULL DEFAULT '',
    response_account_id      varchar(36)    NOT NULL DEFAULT '',
    response_tax_amount      numeric(12, 2) NULL,
    created_at               timestamptz    NOT NULL,
    xero_bill_sync_id        varchar(36)    NOT NULL,
    bill_line_item_id        varchar(36)    NULL,
    CONSTRAINT fk_xero_bill_sync_line_xero_bill_sync_id
        FOREIGN KEY (xero_bill_sync_id)
        REFERENCES pettycashv2.xero_bill_sync(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_xero_bill_sync_line_bill_line_item_id
        FOREIGN KEY (bill_line_item_id)
        REFERENCES pettycashv2.bill_line_item(id)
        ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS xero_bill_sync_line_xero_bill_sync_id_idx
    ON pettycashv2.xero_bill_sync_line(xero_bill_sync_id);
CREATE INDEX IF NOT EXISTS xero_bill_sync_line_bill_line_item_id_idx
    ON pettycashv2.xero_bill_sync_line(bill_line_item_id);

-- ---------------------------------------------------------------------
-- 12) xero_bill_sync_payload -> xero_bill_sync (CASCADE; OneToOne -> UNIQUE)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pettycashv2.xero_bill_sync_payload (
    id                  varchar(36)  NOT NULL PRIMARY KEY,
    request_json        jsonb        NULL,
    response_json       jsonb        NULL,
    request_headers     jsonb        NULL,
    response_headers    jsonb        NULL,
    created_at          timestamptz  NOT NULL,
    updated_at          timestamptz  NOT NULL,
    xero_bill_sync_id   varchar(36)  NOT NULL UNIQUE,
    CONSTRAINT fk_xero_bill_sync_payload_xero_bill_sync_id
        FOREIGN KEY (xero_bill_sync_id)
        REFERENCES pettycashv2.xero_bill_sync(id)
        ON DELETE CASCADE
);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0010_payment_attachment_xero_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql=REPAIR_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
