"""Widen bill amount columns from NUMERIC(12,2) to NUMERIC(14,2).

The old precision capped whole-dollar amounts at 10 integer digits
(max 9,999,999,999.99). Widening to NUMERIC(14,2) allows up to 12
integer digits + 2 decimal places (max 999,999,999,999.99), matching the
12-digit limit enforced on the bill amount input.

Applies to:
    bill.amount                NUMERIC(12,2) -> NUMERIC(14,2)
    bill_line_item.unit_amount NUMERIC(12,2) -> NUMERIC(14,2)
    bill_line_item.line_amount NUMERIC(12,2) -> NUMERIC(14,2)

Increasing numeric precision is lossless: every existing value fits
unchanged. Reverse narrows back to NUMERIC(12,2), which can fail if any
row already exceeds the old range — acceptable for a reverse migration.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0017_align_bill_line_item_with_model"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bill",
            name="amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name="billlineitem",
            name="unit_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name="billlineitem",
            name="line_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
