from django.db import migrations, models


def _pg_set_default(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE pettycashv2.bill ALTER COLUMN xero_account_code SET DEFAULT '';"
        )


def _pg_drop_default(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE pettycashv2.bill ALTER COLUMN xero_account_code DROP DEFAULT;"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0004_entitybillaccountxero_is_deleted"),
    ]

    operations = [
        migrations.AddField(
            model_name="bill",
            name="xero_account_code",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.RunPython(_pg_set_default, _pg_drop_default),
    ]
