# Generated manually for TransmitSMS MMS Webhook model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('transmitsms', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TransmitSMSMMSWebhook',
            fields=[
                ('transmit_account', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True,
                    related_name='mms_webhook_config',
                    serialize=False,
                    to='transmitsms.transmitsmsaccount',
                )),
                ('webhook_id', models.CharField(max_length=255, unique=True)),
                ('webhook_name', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'TransmitSMS MMS Webhook',
                'verbose_name_plural': 'TransmitSMS MMS Webhooks',
            },
        ),
    ]
