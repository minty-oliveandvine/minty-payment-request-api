"""Align pettycashv2.attachment with the Django Attachment model.

The attachment table on STAGING/PRODUCTION carries the legacy Alembic
schema (path, type, size, etc.) while the Django model expects
file_path, mime_type, file_size, plus several additional columns.

Currently blocking /api/v1/bills/draft/ — Django selects file_path and
Postgres reports "column attachment.file_path does not exist".

Changes (idempotent — DO blocks gate every step):
    RENAME    path -> file_path
    RENAME    type -> mime_type   + widen varchar(50)  -> varchar(100)
    RENAME    size -> file_size   + widen integer      -> bigint
    WIDEN     original_name       varchar(200) -> varchar(255)
    WIDEN     stored_name         varchar(150) -> varchar(255)
    ADD       file_extension      varchar(20)  NOT NULL DEFAULT ''
    ADD       storage_provider    varchar(50)  NOT NULL DEFAULT 's3'
    ADD       checksum_sha256     varchar(128) NOT NULL DEFAULT ''
    ADD       updated_at          timestamptz  NOT NULL (backfilled to created_at)
    ADD       is_deleted          boolean      NOT NULL DEFAULT false
    ADD       deleted_at          timestamptz  NULL

Reverse: noop. Restoring the legacy column names + types would require
recreating data shape decisions that don't belong in a migration.
"""
from django.db import migrations


ALIGN_SQL = r"""
-- ---------------------------------------------------------------------
-- 1) Rename legacy columns: path -> file_path, type -> mime_type, size -> file_size
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='path'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='file_path'
    ) THEN
        ALTER TABLE pettycashv2.attachment RENAME COLUMN path TO file_path;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='type'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='mime_type'
    ) THEN
        ALTER TABLE pettycashv2.attachment RENAME COLUMN type TO mime_type;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='size'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='file_size'
    ) THEN
        ALTER TABLE pettycashv2.attachment RENAME COLUMN size TO file_size;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2) Widen / retype renamed columns to match the Django model
-- ---------------------------------------------------------------------
DO $$
DECLARE
    cur_len  integer;
    cur_type text;
BEGIN
    SELECT character_maximum_length INTO cur_len
    FROM information_schema.columns
    WHERE table_schema='pettycashv2' AND table_name='attachment'
      AND column_name='mime_type';
    IF cur_len IS NOT NULL AND cur_len < 100 THEN
        ALTER TABLE pettycashv2.attachment ALTER COLUMN mime_type TYPE varchar(100);
    END IF;

    SELECT data_type INTO cur_type
    FROM information_schema.columns
    WHERE table_schema='pettycashv2' AND table_name='attachment'
      AND column_name='file_size';
    IF cur_type = 'integer' THEN
        ALTER TABLE pettycashv2.attachment ALTER COLUMN file_size TYPE bigint;
    END IF;

    SELECT character_maximum_length INTO cur_len
    FROM information_schema.columns
    WHERE table_schema='pettycashv2' AND table_name='attachment'
      AND column_name='original_name';
    IF cur_len IS NOT NULL AND cur_len < 255 THEN
        ALTER TABLE pettycashv2.attachment ALTER COLUMN original_name TYPE varchar(255);
    END IF;

    SELECT character_maximum_length INTO cur_len
    FROM information_schema.columns
    WHERE table_schema='pettycashv2' AND table_name='attachment'
      AND column_name='stored_name';
    IF cur_len IS NOT NULL AND cur_len < 255 THEN
        ALTER TABLE pettycashv2.attachment ALTER COLUMN stored_name TYPE varchar(255);
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 3) Add columns the model declares that the legacy schema lacks
-- ---------------------------------------------------------------------
ALTER TABLE pettycashv2.attachment
    ADD COLUMN IF NOT EXISTS file_extension   varchar(20)  NOT NULL DEFAULT '';
ALTER TABLE pettycashv2.attachment
    ADD COLUMN IF NOT EXISTS storage_provider varchar(50)  NOT NULL DEFAULT 's3';
ALTER TABLE pettycashv2.attachment
    ADD COLUMN IF NOT EXISTS checksum_sha256  varchar(128) NOT NULL DEFAULT '';
ALTER TABLE pettycashv2.attachment
    ADD COLUMN IF NOT EXISTS is_deleted       boolean      NOT NULL DEFAULT false;
ALTER TABLE pettycashv2.attachment
    ADD COLUMN IF NOT EXISTS deleted_at       timestamptz  NULL;

-- updated_at: add nullable, backfill from created_at, then enforce NOT NULL
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='pettycashv2' AND table_name='attachment'
          AND column_name='updated_at'
    ) THEN
        ALTER TABLE pettycashv2.attachment ADD COLUMN updated_at timestamptz;
        UPDATE pettycashv2.attachment SET updated_at = COALESCE(created_at, NOW());
        ALTER TABLE pettycashv2.attachment ALTER COLUMN updated_at SET NOT NULL;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0015_align_bill_with_model"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ALIGN_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
