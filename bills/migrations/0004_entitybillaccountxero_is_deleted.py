from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bills", "0003_audit"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitybillaccountxero",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
    ]
