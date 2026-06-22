"""Align pettycashv2.bill with the Django Bill model.

The bill table on STAGING/PRODUCTION still carries the legacy Alembic
schema from Module 1: three columns that the Django model does not know
about (xero_invoice_id NOT NULL, paid_date, attachment_id + FK), plus
type/length mismatches on contact, invoice_date, and due_date.

xero_invoice_id NOT NULL is what currently blocks /api/v1/bills/submit/
(Django INSERTs omit the column, Postgres rejects the row). The Xero
invoice id is tracked on xero_bill_sync.response_invoice_id, so the
column on bill is redundant.

Changes (idempotent — IF EXISTS / DO blocks):
    DROP FK   bill.attachment_id -> attachment(id) and its indexes
    DROP COL  bill.attachment_id, bill.xero_invoice_id, bill.paid_date
    ALTER     bill.contact      varchar(50)  -> varchar(100)
    ALTER     bill.invoice_date timestamptz  -> date  (cast via ::date)
    ALTER     bill.due_date     timestamptz  -> date  (cast via ::date)

Reverse: noop. Restoring legacy columns and the original timestamptz
types would require backfill decisions that don't belong in a migration.
"""
from django.db import migrations


ALIGN_SQL = r"""
-- ---------------------------------------------------------------------
-- 1) Drop legacy FK + indexes on attachment_id, then the column itself
-- ---------------------------------------------------------------------
DO $$
DECLARE
    fk_name text;
BEGIN
    SELECT con.conname INTO fk_name
    FROM pg_constraint con
    WHERE con.conrelid = 'pettycashv2.bill'::regclass
      AND con.contype  = 'f'
      AND EXISTS (
          SELECT 1 FROM pg_attribute att
          WHERE att.attrelid = con.conrelid
            AND att.attnum   = ANY (con.conkey)
            AND att.attname  = 'attachment_id'
      )
    LIMIT 1;

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pettycashv2.bill DROP CONSTRAINT %I', fk_name);
    END IF;
END $$;

DROP INDEX IF EXISTS pettycashv2.bill_attachment_id_99e669a1;
DROP INDEX IF EXISTS pettycashv2.bill_attachment_id_99e669a1_like;

ALTER TABLE pettycashv2.bill DROP COLUMN IF EXISTS attachment_id;

-- ---------------------------------------------------------------------
-- 2) Drop legacy columns the Django model does not declare
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.bill DROP COLUMN IF EXISTS xero_invoice_id;
ALTER TABLE pettycashv2.bill DROP COLUMN IF EXISTS paid_date;

-- ---------------------------------------------------------------------
-- 3) Widen contact to match the model (varchar(50) -> varchar(100))
--    Skipped if already at or beyond 100 chars wide.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    cur_len integer;
BEGIN
    SELECT character_maximum_length INTO cur_len
    FROM information_schema.columns
    WHERE table_schema='pettycashv2'
      AND table_name='bill'
      AND column_name='contact';

    IF cur_len IS NOT NULL AND cur_len < 100 THEN
        ALTER TABLE pettycashv2.bill ALTER COLUMN contact TYPE varchar(100);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 4) Convert invoice_date / due_date from timestamptz to date
--    Skipped if already date.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    cur_type text;
BEGIN
    SELECT data_type INTO cur_type
    FROM information_schema.columns
    WHERE table_schema='pettycashv2'
      AND table_name='bill'
      AND column_name='invoice_date';

    IF cur_type = 'timestamp with time zone' THEN
        ALTER TABLE pettycashv2.bill
            ALTER COLUMN invoice_date TYPE date USING invoice_date::date;
    END IF;

    SELECT data_type INTO cur_type
    FROM information_schema.columns
    WHERE table_schema='pettycashv2'
      AND table_name='bill'
      AND column_name='due_date';

    IF cur_type = 'timestamp with time zone' THEN
        ALTER TABLE pettycashv2.bill
            ALTER COLUMN due_date TYPE date USING due_date::date;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0014_widen_entitybillaccountxero_account_code"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ALIGN_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
