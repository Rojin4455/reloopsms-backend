from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sms_management_app", "0003_alter_smsmessage_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="smsmessage",
            name="ghl_sync_error",
            field=models.TextField(blank=True, null=True),
        ),
    ]
