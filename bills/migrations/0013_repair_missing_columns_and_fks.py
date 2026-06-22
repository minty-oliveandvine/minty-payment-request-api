"""Repair migration for environments where 0004, 0005, 0007 and 0008
were marked applied in django_migrations but never executed against the
physical schema (typical of databases originally built by Alembic /
manual SQL and later wired into Django via `migrate --fake`).

What this fixes (idempotently):

  Pre-step -> CREATE TABLE IF NOT EXISTS for core tables that 0001
              was supposed to create but didn't (entity_bill_account_xero).
              Schema baked includes 0004's is_deleted column.

  0004 -> entity_bill_account_xero.is_deleted (no-op if pre-step created
          the table fresh; otherwise ADD COLUMN IF NOT EXISTS)
  0005 -> bill.xero_account_code
  0007 -> 13 CASCADE + 1 SET NULL FK ON DELETE rules
  0008 -> bill_attachment.xero_attachment_id
          bill_attachment.xero_filename

Every ADD COLUMN statement is guarded by a table-existence check, so
the migration is safe even if some other table is also physically
missing — it logs and skips rather than crashing.

On environments where these were already applied correctly, every
operation is a no-op.
"""
import logging

from django.db import migrations

logger = logging.getLogger("minty-api")

SCHEMA = "pettycashv2"


# Tables that 0001_initial declared but might be physically missing on
# Alembic-origin databases. CREATE TABLE IF NOT EXISTS is safe to run
# even when the table is already there.
CREATE_MISSING_CORE_TABLES_SQL = r"""
-- entity_bill_account_xero (0001) + is_deleted column from 0004 baked in
CREATE TABLE IF NOT EXISTS pettycashv2.entity_bill_account_xero (
    id                varchar(36)  NOT NULL PRIMARY KEY,
    entity_id         varchar(36)  NOT NULL,
    account_code      varchar(20)  NOT NULL,
    account_name      varchar(150) NOT NULL DEFAULT '',
    account_type      varchar(50)  NOT NULL DEFAULT '',
    is_default        boolean      NOT NULL DEFAULT false,
    is_active         boolean      NOT NULL DEFAULT true,
    is_deleted        boolean      NOT NULL DEFAULT false,
    xero_account_id   varchar(36)  NOT NULL DEFAULT '',
    sort_order        integer      NOT NULL DEFAULT 0,
    created_by        varchar(36)  NOT NULL DEFAULT '',
    created_at        timestamptz  NOT NULL,
    updated_at        timestamptz  NOT NULL
);
CREATE INDEX IF NOT EXISTS entity_bill_account_xero_entity_id_idx
    ON pettycashv2.entity_bill_account_xero(entity_id);
"""


# (table, column, column-def)
COLUMN_REPAIRS = [
    ("entity_bill_account_xero", "is_deleted",         "boolean NOT NULL DEFAULT false"),
    ("bill",                     "xero_account_code",  "varchar(20) NOT NULL DEFAULT ''"),
    ("bill_attachment",          "xero_attachment_id", "varchar(36) NOT NULL DEFAULT ''"),
    ("bill_attachment",          "xero_filename",      "varchar(255) NOT NULL DEFAULT ''"),
]


CASCADE_FKS = [
    ("audit", "bill_id", "bill", "id"),
    ("bill_line_item", "bill_id", "bill", "id"),
    ("bill_attachment", "bill_id", "bill", "id"),
    ("bill_attachment", "attachment_id", "attachment", "id"),
    ("payment", "bill_id", "bill", "id"),
    ("payment_attachment", "payment_id", "payment", "id"),
    ("payment_attachment", "attachment_id", "attachment", "id"),
    ("entity_function_map", "entity_function_id", "entity_function", "id"),
    ("entity_bill_currency", "currency_info_id", "currency_info", "id"),
    ("xero_bill_sync", "bill_id", "bill", "id"),
    ("xero_bill_sync_line", "xero_bill_sync_id", "xero_bill_sync", "id"),
    ("xero_bill_sync_payload", "xero_bill_sync_id", "xero_bill_sync", "id"),
    ("xero_bill_response_line", "xero_bill_sync_id", "xero_bill_sync", "id"),
]

SET_NULL_FKS = [
    ("xero_bill_sync_line", "bill_line_item_id", "bill_line_item", "id"),
]


def _table_exists(cursor, table):
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        [SCHEMA, table],
    )
    return cursor.fetchone() is not None


def _fk_on_delete(cursor, table, column):
    """Return (constraint_name, confdeltype) for the FK on `table.column`,
    or (None, None) if no FK exists. confdeltype: 'c'=CASCADE, 'n'=SET NULL,
    'a'=NO ACTION, 'r'=RESTRICT, 'd'=SET DEFAULT.
    """
    cursor.execute(
        """
        SELECT con.conname, con.confdeltype
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        JOIN pg_attribute att ON att.attrelid = con.conrelid
                             AND att.attnum = ANY(con.conkey)
        WHERE nsp.nspname = %s
          AND rel.relname = %s
          AND att.attname = %s
          AND con.contype = 'f'
        LIMIT 1
        """,
        [SCHEMA, table, column],
    )
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)


def repair_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    cursor = schema_editor.connection.cursor()
    for table, column, definition in COLUMN_REPAIRS:
        if not _table_exists(cursor, table):
            logger.warning(
                "Table %s.%s missing — skipping ADD COLUMN %s",
                SCHEMA, table, column,
            )
            continue
        cursor.execute(
            f"ALTER TABLE {SCHEMA}.{table} "
            f"ADD COLUMN IF NOT EXISTS {column} {definition};"
        )


def _ensure_fk(cursor, table, column, ref_table, ref_column, on_delete):
    """Ensure the FK on table.column references ref_table.ref_column with
    the desired ON DELETE rule. No-op if already correct. Drops & replaces
    if a different rule is in place. Skips silently if the table or
    referenced table is missing.
    """
    if not _table_exists(cursor, table):
        logger.info("Table %s.%s missing — skipping FK on %s", SCHEMA, table, column)
        return
    if not _table_exists(cursor, ref_table):
        logger.info(
            "Referenced table %s.%s missing — skipping FK %s.%s",
            SCHEMA, ref_table, table, column,
        )
        return

    desired_code = {"CASCADE": "c", "SET NULL": "n"}[on_delete]
    existing_name, existing_code = _fk_on_delete(cursor, table, column)

    if existing_name and existing_code == desired_code:
        return

    if existing_name:
        cursor.execute(
            f'ALTER TABLE {SCHEMA}.{table} DROP CONSTRAINT "{existing_name}";'
        )

    new_name = f"fk_{table}_{column}"
    cursor.execute(
        f'ALTER TABLE {SCHEMA}.{table} ADD CONSTRAINT "{new_name}" '
        f"FOREIGN KEY ({column}) REFERENCES {SCHEMA}.{ref_table}({ref_column}) "
        f"ON DELETE {on_delete};"
    )


def repair_fks(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    cursor = schema_editor.connection.cursor()
    for tbl, col, ref_tbl, ref_col in CASCADE_FKS:
        _ensure_fk(cursor, tbl, col, ref_tbl, ref_col, "CASCADE")
    for tbl, col, ref_tbl, ref_col in SET_NULL_FKS:
        _ensure_fk(cursor, tbl, col, ref_tbl, ref_col, "SET NULL")


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0012_update_currency_info_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_MISSING_CORE_TABLES_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(repair_columns, migrations.RunPython.noop),
        migrations.RunPython(repair_fks, migrations.RunPython.noop),
    ]
