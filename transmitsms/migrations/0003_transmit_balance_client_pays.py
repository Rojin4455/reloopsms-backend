from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("transmitsms", "0002_transmitsmsmmswebhook"),
    ]

    operations = [
        migrations.AddField(
            model_name="transmitsmsaccount",
            name="balance_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="transmitsmsaccount",
            name="client_pays",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="TransmitAgencyBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("balance", models.DecimalField(decimal_places=2, default=0.0, max_digits=10)),
                ("currency", models.CharField(default="AUD", max_length=10)),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Transmit agency balance snapshot",
                "verbose_name_plural": "Transmit agency balance snapshots",
            },
        ),
    ]
