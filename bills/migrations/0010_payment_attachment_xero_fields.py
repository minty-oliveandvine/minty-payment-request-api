# Generated for the bank-slip republish dedup fix.
#
# Adds two tracking columns to payment_attachment so upload_bankslip_to_xero
# can run as a delete-then-upload full replace and avoid stacking duplicate
# bank-slip files on the Xero invoice across multiple republishes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bills', '0009_alter_bill_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentattachment',
            name='xero_attachment_id',
            field=models.CharField(
                blank=True,
                default='',
                max_length=36,
            ),
        ),
        migrations.AddField(
            model_name='paymentattachment',
            name='xero_filename',
            field=models.CharField(
                blank=True,
                default='',
                max_length=255,
            ),
        ),
    ]
