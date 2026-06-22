"""Fix 14 FK ON DELETE constraints to match model definitions.

All Django-managed FK constraints were NO ACTION in the database.
Models define on_delete=CASCADE (13 FKs) and on_delete=SET_NULL (1 FK).

This migration looks up the actual constraint name from pg_constraint
and replaces it with the correct ON DELETE rule. Skips silently if the
FK constraint or table does not exist yet.

FK Constraints Fixed:
  CASCADE (13):
    1.  audit.bill_id                          -> bill.id
    2.  bill_line_item.bill_id                 -> bill.id
    3.  bill_attachment.bill_id                -> bill.id
    4.  bill_attachment.attachment_id           -> attachment.id
    5.  payment.bill_id                        -> bill.id
    6.  payment_attachment.payment_id           -> payment.id
    7.  payment_attachment.attachment_id         -> attachment.id
    8.  entity_function_map.entity_function_id  -> entity_function.id
    9.  entity_bill_currency.currency_info_id   -> currency_info.id
    10. xero_bill_sync.bill_id                 -> bill.id
    11. xero_bill_sync_line.xero_bill_sync_id  -> xero_bill_sync.id
    12. xero_bill_sync_payload.xero_bill_sync_id -> xero_bill_sync.id
    13. xero_bill_response_line.xero_bill_sync_id -> xero_bill_sync.id

  SET NULL (1):
    14. xero_bill_sync_line.bill_line_item_id  -> bill_line_item.id
"""
import logging

from django.db import migrations

logger = logging.getLogger("minty-api")

SCHEMA = "pettycashv2"

CASCADE_FKS = [
    # (table, column, ref_table, ref_column)
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


def _find_fk_constraint(cursor, table, column):
    """Look up the actual FK constraint name from pg_constraint."""
    cursor.execute(
        """
        SELECT con.conname
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
    return row[0] if row else None


def _replace_fk(cursor, table, column, ref_table, ref_column, on_delete):
    """Drop the existing FK (by lookup) and recreate with the desired ON DELETE rule."""
    old_name = _find_fk_constraint(cursor, table, column)
    if not old_name:
        logger.info("No FK found for %s.%s — skipping drop", table, column)
    else:
        cursor.execute(
            f'ALTER TABLE {SCHEMA}.{table} DROP CONSTRAINT "{old_name}";'
        )

    new_name = f"fk_{table}_{column}"
    cursor.execute(
        f'ALTER TABLE {SCHEMA}.{table} ADD CONSTRAINT "{new_name}" '
        f"FOREIGN KEY ({column}) REFERENCES {SCHEMA}.{ref_table}({ref_column}) "
        f"ON DELETE {on_delete};"
    )


def forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    cursor = schema_editor.connection.cursor()
    for tbl, col, ref_tbl, ref_col in CASCADE_FKS:
        _replace_fk(cursor, tbl, col, ref_tbl, ref_col, "CASCADE")
    for tbl, col, ref_tbl, ref_col in SET_NULL_FKS:
        _replace_fk(cursor, tbl, col, ref_tbl, ref_col, "SET NULL")


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    cursor = schema_editor.connection.cursor()
    for tbl, col, ref_tbl, ref_col in CASCADE_FKS + SET_NULL_FKS:
        _replace_fk(cursor, tbl, col, ref_tbl, ref_col, "NO ACTION")


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0006_alter_audit_action_choices"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
