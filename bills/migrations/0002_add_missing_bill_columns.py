from django.db import connection, migrations


def add_columns_if_missing(apps, schema_editor):
    vendor = connection.vendor
    if vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA table_info(bill)")
        existing = {row[1] for row in cursor.fetchall()}
        cols = [
            ("reference", "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("currency_code", "VARCHAR(10) NOT NULL DEFAULT ''"),
            ("published", "VARCHAR(30) NOT NULL DEFAULT 'not_published'"),
        ]
        for name, definition in cols:
            if name not in existing:
                cursor.execute(f"ALTER TABLE bill ADD COLUMN {name} {definition}")
    else:
        cursor = connection.cursor()
        cursor.execute("""
            ALTER TABLE bill ADD COLUMN IF NOT EXISTS reference VARCHAR(255) NOT NULL DEFAULT '';
            ALTER TABLE bill ADD COLUMN IF NOT EXISTS currency_code VARCHAR(10) NOT NULL DEFAULT '';
            ALTER TABLE bill ADD COLUMN IF NOT EXISTS published VARCHAR(30) NOT NULL DEFAULT 'not_published';
        """)


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_columns_if_missing, migrations.RunPython.noop),
    ]
