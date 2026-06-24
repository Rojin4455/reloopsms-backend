from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sms_management_app", "0005_scrub_polluted_error_messages"),
    ]

    operations = [
        migrations.AddField(
            model_name="smsmessage",
            name="error_category",
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
    ]
