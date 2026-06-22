"""Widen entity_bill_account_xero.account_code from VARCHAR(20) to VARCHAR(150).

Some Xero entities populate the account "Code" field with descriptive text
longer than 20 characters (e.g. "Business Integrated HKD Current"), which
overflows the original column and aborts the CoA sync transaction from
Module 1. 150 matches account_name and is well above any realistic Xero
code length we've observed.

This migration touches only entity_bill_account_xero.account_code.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0013_repair_missing_columns_and_fks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="entitybillaccountxero",
            name="account_code",
            field=models.CharField(max_length=150),
        ),
    ]
