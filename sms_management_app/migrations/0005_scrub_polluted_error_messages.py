from django.db import migrations
from django.db.models import Q


def scrub_polluted_errors(apps, schema_editor):
    """
    Older rows had their real failure reason overwritten by the GHL status-sync
    task's retry-exhaustion message, which also embedded the GHL access token.

    The genuine reason is unrecoverable for those rows, so we clear the misleading
    (and token-leaking) text and leave an honest placeholder. New rows are handled
    correctly by writing sync issues to ghl_sync_error instead.
    """
    SMSMessage = apps.get_model("sms_management_app", "SMSMessage")

    polluted = SMSMessage.objects.filter(
        Q(error_message__icontains="Can't retry")
        | Q(error_message__icontains="GHL update failed")
    )
    polluted.update(
        error_message="Delivery failed (original reason not captured)",
        ghl_sync_error="GHL status sync previously failed (details cleared)",
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sms_management_app", "0004_smsmessage_ghl_sync_error"),
    ]

    operations = [
        migrations.RunPython(scrub_polluted_errors, noop),
    ]
