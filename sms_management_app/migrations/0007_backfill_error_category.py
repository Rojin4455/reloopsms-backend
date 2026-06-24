from django.db import migrations


def backfill_categories(apps, schema_editor):
    """Categorize all existing failed messages from their stored error_message."""
    from sms_management_app.error_utils import categorize_failure

    SMSMessage = apps.get_model("sms_management_app", "SMSMessage")

    qs = SMSMessage.objects.filter(status="failed").exclude(
        error_message__isnull=True
    ).only("id", "error_message", "error_category")

    batch = []
    for msg in qs.iterator():
        category = categorize_failure(msg.error_message)
        if msg.error_category != category:
            msg.error_category = category
            batch.append(msg)
        if len(batch) >= 1000:
            SMSMessage.objects.bulk_update(batch, ["error_category"])
            batch = []
    if batch:
        SMSMessage.objects.bulk_update(batch, ["error_category"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sms_management_app", "0006_smsmessage_error_category"),
    ]

    operations = [
        migrations.RunPython(backfill_categories, noop),
    ]
