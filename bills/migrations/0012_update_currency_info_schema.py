"""Migrate pettycashv2.currency_info from the legacy Alembic schema
(e914bd416632) to the schema expected by the Django CurrencyInfo model
and PRESTAGING Alembic c1881897f7c3.

Legacy schema (STAGING):
    currency_id      varchar(10)  PRIMARY KEY     -- e.g. 'HKD'
    currency_name    varchar(50)  NOT NULL
    currency_symbol  varchar(10)  NULL
    iso_code         varchar(3)   NULL UNIQUE

Target schema (Django + PRESTAGING):
    id              varchar(36)   PRIMARY KEY     -- UUID
    currency_code   varchar(10)   NOT NULL UNIQUE -- formerly currency_id
    currency_name   varchar(100)  NOT NULL        -- widened from 50
    symbol          varchar(10)   NOT NULL DEFAULT ''  -- formerly currency_symbol
    decimal_places  integer       NOT NULL DEFAULT 2
    is_active       boolean       NOT NULL DEFAULT true
    created_at      timestamptz   NOT NULL
    updated_at      timestamptz   NOT NULL

Data preservation:
    new id            = gen_random_uuid()::text
    new currency_code = old currency_id        (e.g. 'HKD')
    new currency_name = old currency_name      (now varchar(100))
    new symbol        = COALESCE(currency_symbol, '')
    decimal_places    = 2
    is_active         = true
    created_at/updated_at = NOW()
    iso_code          = dropped (was redundant with currency_id in practice)

FK redirection:
    country_info.currency_id was FK -> currency_info(currency_id);
    after this migration it is FK -> currency_info(currency_code).

Also adds the deferred FK that 0011 left out:
    entity_bill_currency.currency_info_id -> currency_info(id) ON DELETE CASCADE.

Idempotent: if currency_info already has the new schema (e.g. on
PRESTAGING / PRODUCTION), every statement is a no-op via IF (NOT) EXISTS
guards and DO blocks that look up current state.

Note: requires PostgreSQL 13+ for gen_random_uuid() built-in. If running
on an older Postgres, install pgcrypto first:
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
"""
from django.db import migrations


MIGRATE_SQL = r"""
-- ---------------------------------------------------------------------
-- Step 1. Add the new columns (nullable initially so existing rows survive)
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS id              varchar(36);
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS currency_code   varchar(10);
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS symbol          varchar(10);
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS decimal_places  integer;
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS is_active       boolean;
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS created_at      timestamptz;
ALTER TABLE pettycashv2.currency_info ADD COLUMN IF NOT EXISTS updated_at      timestamptz;

-- ---------------------------------------------------------------------
-- Step 2. Backfill new columns from legacy columns (and from defaults)
-- ---------------------------------------------------------------------
UPDATE pettycashv2.currency_info SET id = gen_random_uuid()::text WHERE id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'pettycashv2'
          AND table_name = 'currency_info'
          AND column_name = 'currency_id'
    ) THEN
        EXECUTE 'UPDATE pettycashv2.currency_info SET currency_code = currency_id WHERE currency_code IS NULL';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'pettycashv2'
          AND table_name = 'currency_info'
          AND column_name = 'currency_symbol'
    ) THEN
        EXECUTE 'UPDATE pettycashv2.currency_info SET symbol = COALESCE(currency_symbol, '''') WHERE symbol IS NULL';
    END IF;
END $$;

UPDATE pettycashv2.currency_info SET symbol         = ''      WHERE symbol         IS NULL;
UPDATE pettycashv2.currency_info SET decimal_places = 2       WHERE decimal_places IS NULL;
UPDATE pettycashv2.currency_info SET is_active      = true    WHERE is_active      IS NULL;
UPDATE pettycashv2.currency_info SET created_at     = NOW()   WHERE created_at     IS NULL;
UPDATE pettycashv2.currency_info SET updated_at     = NOW()   WHERE updated_at     IS NULL;

-- ---------------------------------------------------------------------
-- Step 3. Drop legacy FK from country_info.currency_id -> currency_info(currency_id)
--         (we'll re-create it pointing at currency_code at the end)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    fk_name text;
BEGIN
    SELECT con.conname INTO fk_name
    FROM pg_constraint con
    JOIN pg_class rel        ON rel.oid = con.conrelid
    JOIN pg_namespace nsp    ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'pettycashv2'
      AND rel.relname = 'country_info'
      AND con.contype = 'f'
      AND con.confrelid = 'pettycashv2.currency_info'::regclass
    LIMIT 1;

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pettycashv2.country_info DROP CONSTRAINT %I', fk_name);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Step 4. Drop legacy PK if it is on currency_id, leaving the table
--         temporarily with no PK so we can swap to the new id column
-- ---------------------------------------------------------------------
DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT con.conname INTO pk_name
    FROM pg_constraint con
    JOIN pg_attribute att
      ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    WHERE con.conrelid = 'pettycashv2.currency_info'::regclass
      AND con.contype = 'p'
      AND att.attname = 'currency_id'
    LIMIT 1;

    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pettycashv2.currency_info DROP CONSTRAINT %I', pk_name);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Step 5. Make new columns NOT NULL with defaults (matches Django model)
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.currency_info ALTER COLUMN id              SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN currency_code   SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN symbol          SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN symbol          SET DEFAULT '';
ALTER TABLE pettycashv2.currency_info ALTER COLUMN decimal_places  SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN decimal_places  SET DEFAULT 2;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN is_active       SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN is_active       SET DEFAULT true;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN created_at      SET NOT NULL;
ALTER TABLE pettycashv2.currency_info ALTER COLUMN updated_at      SET NOT NULL;

-- ---------------------------------------------------------------------
-- Step 6. Widen currency_name to varchar(100) (was varchar(50) in legacy)
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.currency_info ALTER COLUMN currency_name TYPE varchar(100);

-- ---------------------------------------------------------------------
-- Step 7. Add new PK on id (skip if a PK already exists, e.g. PRESTAGING)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'pettycashv2.currency_info'::regclass
          AND contype  = 'p'
    ) THEN
        ALTER TABLE pettycashv2.currency_info ADD PRIMARY KEY (id);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Step 8. Add UNIQUE on currency_code (skip if already present)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        JOIN pg_attribute  att
          ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
        WHERE con.conrelid = 'pettycashv2.currency_info'::regclass
          AND con.contype  = 'u'
          AND att.attname  = 'currency_code'
    ) THEN
        ALTER TABLE pettycashv2.currency_info
            ADD CONSTRAINT currency_info_currency_code_key UNIQUE (currency_code);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Step 9. Drop legacy columns
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.currency_info DROP COLUMN IF EXISTS currency_id;
ALTER TABLE pettycashv2.currency_info DROP COLUMN IF EXISTS currency_symbol;
ALTER TABLE pettycashv2.currency_info DROP COLUMN IF EXISTS iso_code;

-- ---------------------------------------------------------------------
-- Step 10. Restore FK from country_info.currency_id, now pointing at
--          currency_info(currency_code) (skip if it's already there)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'pettycashv2' AND table_name = 'country_info'
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid  = 'pettycashv2.country_info'::regclass
          AND con.contype   = 'f'
          AND con.confrelid = 'pettycashv2.currency_info'::regclass
    ) THEN
        ALTER TABLE pettycashv2.country_info
            ADD CONSTRAINT fk_country_info_currency_id
            FOREIGN KEY (currency_id)
            REFERENCES pettycashv2.currency_info(currency_code);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Step 11. Add the deferred FK that 0011 omitted:
--          entity_bill_currency.currency_info_id -> currency_info(id)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'pettycashv2' AND table_name = 'entity_bill_currency'
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid = 'pettycashv2.entity_bill_currency'::regclass
          AND con.contype  = 'f'
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


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0011_repair_missing_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=MIGRATE_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
