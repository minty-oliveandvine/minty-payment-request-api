"""Align entity_bill_currency.currency_info_id with the uuid currency_info PK
and add the FK that 0011/0012 deferred.

pettycashv2.currency_info was rebuilt by the Flask app's Alembic migration
c8e0a2b4d6f8 with ``id uuid PRIMARY KEY`` (a real uuid column, not the
varchar(36) that 0012 targeted). A foreign key cannot span varchar -> uuid,
so before adding the deferred FK this migration converts
``entity_bill_currency.currency_info_id`` from varchar(36) to uuid
(USING cast — values are already uuid strings).

Hard prerequisite: currency_info must already have the uuid ``id`` column.
If it doesn't (e.g. an environment where the Alembic migration hasn't run
yet), this migration RAISES instead of silently no-oping, so it can't be
recorded as applied without doing its job. Run Minty's
``flask db upgrade`` (>= c8e0a2b4d6f8) first.

Idempotent on re-run: the type conversion and FK add are both guarded.

State operations mirror the model changes (CurrencyInfo.id -> UUIDField);
the FK column type on EntityBillCurrency follows the target PK
automatically. Tests are unaffected (pytest runs --no-migrations).
"""
import uuid

from django.db import migrations, models


MIGRATE_SQL = r"""
-- Prerequisite: the rebuilt currency_info (uuid PK) must be in place.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'pettycashv2'
          AND table_name   = 'currency_info'
          AND column_name  = 'id'
          AND data_type    = 'uuid'
    ) THEN
        RAISE EXCEPTION
            'pettycashv2.currency_info.id is not uuid — run the Flask app''s '
            'Alembic migration c8e0a2b4d6f8 before this one.';
    END IF;
END $$;

-- Convert the FK column to uuid (skip if already converted).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'pettycashv2'
          AND table_name   = 'entity_bill_currency'
          AND column_name  = 'currency_info_id'
          AND data_type   <> 'uuid'
    ) THEN
        ALTER TABLE pettycashv2.entity_bill_currency
            ALTER COLUMN currency_info_id TYPE uuid
            USING currency_info_id::uuid;
    END IF;
END $$;

-- Add the FK 0011/0012 deferred (skip if any FK to currency_info exists).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'pettycashv2'
          AND table_name   = 'entity_bill_currency'
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid  = 'pettycashv2.entity_bill_currency'::regclass
          AND con.contype   = 'f'
          AND con.confrelid = 'pettycashv2.currency_info'::regclass
    ) THEN
        ALTER TABLE pettycashv2.entity_bill_currency
            ADD CONSTRAINT fk_entity_bill_currency_currency_info_id
            FOREIGN KEY (currency_info_id)
            REFERENCES pettycashv2.currency_info(id)
            ON DELETE CASCADE;
    END IF;
END $$;
"""

REVERSE_SQL = r"""
ALTER TABLE pettycashv2.entity_bill_currency
    DROP CONSTRAINT IF EXISTS fk_entity_bill_currency_currency_info_id;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'pettycashv2'
          AND table_name   = 'entity_bill_currency'
          AND column_name  = 'currency_info_id'
          AND data_type    = 'uuid'
    ) THEN
        ALTER TABLE pettycashv2.entity_bill_currency
            ALTER COLUMN currency_info_id TYPE varchar(36)
            USING currency_info_id::text;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0018_widen_amount_to_numeric_14_2"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="currencyinfo",
                    name="id",
                    field=models.UUIDField(
                        primary_key=True, default=uuid.uuid4, serialize=False
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(sql=MIGRATE_SQL, reverse_sql=REVERSE_SQL),
            ],
        ),
    ]
