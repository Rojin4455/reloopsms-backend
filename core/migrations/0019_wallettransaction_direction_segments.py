import re

from django.db import migrations, models


SEGMENT_PATTERN = re.compile(r"\((\d+)\s*segments?\)", re.IGNORECASE)


def _parse_direction(description):
    if not description:
        return None
    lower = description.lower()
    if "inbound" in lower:
        return "inbound"
    if "outbound" in lower:
        return "outbound"
    return None


def _parse_segments(description):
    if not description:
        return None
    match = SEGMENT_PATTERN.search(description)
    return int(match.group(1)) if match else None


def backfill_wallettransaction_direction_segments(apps, schema_editor):
    WalletTransaction = apps.get_model("core", "WalletTransaction")
    SMSMessage = apps.get_model("sms_management_app", "SMSMessage")

    sms_by_id = {}
    for sms in SMSMessage.objects.only("id", "direction", "segments"):
        sms_by_id[str(sms.id)] = sms

    updates = []
    for txn in WalletTransaction.objects.all().iterator():
        direction = txn.direction
        segments = txn.segments

        if direction is None:
            direction = _parse_direction(txn.description)

        if segments is None:
            segments = _parse_segments(txn.description)

        if txn.reference_id and (direction is None or segments is None):
            sms = sms_by_id.get(str(txn.reference_id))
            if sms:
                if direction is None:
                    direction = sms.direction
                if segments is None:
                    segments = sms.segments

        if direction != txn.direction or segments != txn.segments:
            txn.direction = direction
            txn.segments = segments
            updates.append(txn)

        if len(updates) >= 500:
            WalletTransaction.objects.bulk_update(updates, ["direction", "segments"])
            updates = []

    if updates:
        WalletTransaction.objects.bulk_update(updates, ["direction", "segments"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_ghlauthcredentials_ghl_contact_email"),
        ("sms_management_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="wallettransaction",
            name="direction",
            field=models.CharField(
                blank=True,
                choices=[("inbound", "Inbound"), ("outbound", "Outbound")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="wallettransaction",
            name="segments",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_wallettransaction_direction_segments,
            migrations.RunPython.noop,
        ),
    ]
