"""Align pettycashv2.bill_line_item with the Django BillLineItem model.

Legacy Alembic schema (6 cols): id, description varchar(255),
amount numeric, xero_account_id varchar(100), xero_account_name
varchar(150), bill_id.

Django model expects 13 cols: id, bill_id, description TEXT, quantity
NUMERIC(12,4) DEFAULT 1, unit_amount NUMERIC(12,2) DEFAULT 0,
line_amount NUMERIC(12,2) DEFAULT 0, account_code varchar(20),
account_name varchar(150), tax_type varchar(30), sort_order INT,
note TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ.

Currently blocking /api/v1/bills/draft/ — Django selects `quantity` and
Postgres reports it doesn't exist.

Table is empty in this environment, so the three legacy columns
(`amount`, `xero_account_id`, `xero_account_name`) are dropped rather
than mapped — `amount` doesn't have a single equivalent (model splits
into unit_amount + line_amount + quantity) and the xero_account_* pair
is replaced by the model's account_code/account_name.

Changes (idempotent — IF EXISTS / DO blocks):
    DROP COL  amount, xero_account_id, xero_account_name
    ALTER     description varchar(255) -> text
    ADD       quantity      NUMERIC(12,4) NOT NULL DEFAULT 1
    ADD       unit_amount   NUMERIC(12,2) NOT NULL DEFAULT 0
    ADD       line_amount   NUMERIC(12,2) NOT NULL DEFAULT 0
    ADD       account_code  varchar(20)   NOT NULL DEFAULT ''
    ADD       account_name  varchar(150)  NOT NULL DEFAULT ''
    ADD       tax_type      varchar(30)   NOT NULL DEFAULT ''
    ADD       sort_order    integer       NOT NULL DEFAULT 0
    ADD       note          text          NOT NULL DEFAULT ''
    ADD       created_at    timestamptz   NOT NULL (backfilled to NOW())
    ADD       updated_at    timestamptz   NOT NULL (backfilled to NOW())

Reverse: noop.
"""
from django.db import migrations


ALIGN_SQL = r"""
-- ---------------------------------------------------------------------
-- 1) Drop legacy columns with no clean mapping to the model
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.bill_line_item DROP COLUMN IF EXISTS amount;
ALTER TABLE pettycashv2.bill_line_item DROP COLUMN IF EXISTS xero_account_id;
ALTER TABLE pettycashv2.bill_line_item DROP COLUMN IF EXISTS xero_account_name;

-- ---------------------------------------------------------------------
-- 2) Retype description from varchar(255) to text
-- ---------------------------------------------------------------------
DO $$
DECLARE
    cur_type text;
BEGIN
    SELECT data_type INTO cur_type
    FROM information_schema.columns
    WHERE table_schema='pettycashv2' AND table_name='bill_line_item'
      AND column_name='description';
    IF cur_type = 'character varying' THEN
        ALTER TABLE pettycashv2.bill_line_item ALTER COLUMN description TYPE text;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 3) Add columns the model declares (with defaults so existing rows survive)
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS quantity     numeric(12,4) NOT NULL DEFAULT 1;
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS unit_amount  numeric(12,2) NOT NULL DEFAULT 0;
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS line_amount  numeric(12,2) NOT NULL DEFAULT 0;
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS account_code varchar(20)   NOT NULL DEFAULT '';
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS account_name varchar(150)  NOT NULL DEFAULT '';
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS tax_type     varchar(30)   NOT NULL DEFAULT '';
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS sort_order   integer       NOT NULL DEFAULT 0;
ALTER TABLE pettycashv2.bill_line_item
    ADD COLUMN IF NOT EXISTS note         text          NOT NULL DEFAULT '';

-- created_at / updated_at: add nullable, backfill, then enforce NOT NULL
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='bill_line_item'
          AND column_name='created_at'
    ) THEN
        ALTER TABLE pettycashv2.bill_line_item ADD COLUMN created_at timestamptz;
        UPDATE pettycashv2.bill_line_item SET created_at = NOW() WHERE created_at IS NULL;
        ALTER TABLE pettycashv2.bill_line_item ALTER COLUMN created_at SET NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='bill_line_item'
          AND column_name='updated_at'
    ) THEN
        ALTER TABLE pettycashv2.bill_line_item ADD COLUMN updated_at timestamptz;
        UPDATE pettycashv2.bill_line_item SET updated_at = NOW() WHERE updated_at IS NULL;
        ALTER TABLE pettycashv2.bill_line_item ALTER COLUMN updated_at SET NOT NULL;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0016_align_attachment_with_model"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ALIGN_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
