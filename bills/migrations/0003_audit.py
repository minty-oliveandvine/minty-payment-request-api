import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0002_add_missing_bill_columns"),
    ]

    operations = [
        migrations.CreateModel(
            name="Audit",
            fields=[
                ("id", models.CharField(default=uuid.uuid4, max_length=36, primary_key=True, serialize=False)),
                ("action", models.CharField(
                    choices=[
                        ("created", "Created"),
                        ("edited", "Edited"),
                        ("submitted", "Submitted"),
                        ("marked_paid", "Marked Paid"),
                        ("published_to_xero", "Published to Xero"),
                        ("attachment_uploaded", "Attachment Uploaded"),
                        ("attachment_deleted", "Attachment Deleted"),
                    ],
                    max_length=100,
                )),
                ("detail", models.TextField(blank=True, default="")),
                ("date", models.DateTimeField(auto_now_add=True)),
                ("user_id", models.CharField(max_length=36)),
                ("bill", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="audits",
                    to="bills.bill",
                )),
            ],
            options={
                "db_table": "audit",
                "ordering": ["date"],
                "indexes": [
                    models.Index(fields=["bill"], name="idx_audit_bill_id"),
                    models.Index(fields=["date"], name="idx_audit_date"),
                ],
            },
        ),
    ]
