from django.db import migrations
from django.db.models import Q


def scrub_token_leaks(apps, schema_editor):
    SMSMessage = apps.get_model("sms_management_app", "SMSMessage")

    polluted_filter = (
        Q(error_message__icontains="Can't retry")
        | Q(error_message__icontains="GHL update failed")
        | Q(error_message__icontains="ghl_token")
        | Q(error_message__icontains="eyJ")
        | Q(ghl_sync_error__icontains="Can't retry")
        | Q(ghl_sync_error__icontains="ghl_token")
        | Q(ghl_sync_error__icontains="eyJ")
    )

    for msg in SMSMessage.objects.filter(polluted_filter).iterator():
        updates = {}

        if msg.error_message and _needs_scrub(msg.error_message):
            updates["error_message"] = "Delivery failed (original reason not captured)"

        if msg.ghl_sync_error and _needs_scrub(msg.ghl_sync_error):
            updates["ghl_sync_error"] = "GHL status sync failed (details cleared)"

        if updates:
            SMSMessage.objects.filter(pk=msg.pk).update(**updates)


def _needs_scrub(text):
    lower = (text or "").lower()
    return (
        "can't retry" in lower
        or "ghl update failed" in lower
        or "ghl_token" in lower
        or "eyj" in lower
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sms_management_app", "0007_backfill_error_category"),
    ]

    operations = [
        migrations.RunPython(scrub_token_leaks, noop),
    ]
