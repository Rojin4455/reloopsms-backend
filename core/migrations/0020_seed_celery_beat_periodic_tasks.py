from django.db import migrations
from django.utils import timezone


TASK_NAMES = [
    "make-api-call-every-6-hours",
    "make-api-call-for-agency-every-6-hours",
    "sync-contact-wallet-custom-fields-every-10-hours",
    "make-api-call-for-sync_numbers",
    "sync-client-owned-numbers",
]


def _get_crontab(CrontabSchedule, minute, hour):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute),
        hour=str(hour),
        day_of_month="*",
        month_of_year="*",
        day_of_week="*",
        timezone="UTC",
    )
    return schedule


def seed_celery_beat_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    every_10h_0 = _get_crontab(CrontabSchedule, 0, "*/10")
    every_10h_15 = _get_crontab(CrontabSchedule, 15, "*/10")
    every_10h_25 = _get_crontab(CrontabSchedule, 25, "*/10")
    daily_midnight = _get_crontab(CrontabSchedule, 0, 0)
    daily_0030 = _get_crontab(CrontabSchedule, 30, 0)

    task_specs = [
        {
            "name": "make-api-call-every-6-hours",
            "task": "core.tasks.make_api_call",
            "crontab": every_10h_0,
            "queue": "critical",
            "description": "Refresh GHL location OAuth tokens (critical queue).",
        },
        {
            "name": "make-api-call-for-agency-every-6-hours",
            "task": "core.tasks.make_api_call_for_agency_token",
            "crontab": every_10h_15,
            "queue": "critical",
            "description": "Refresh GHL agency OAuth tokens (critical queue).",
        },
        {
            "name": "sync-contact-wallet-custom-fields-every-10-hours",
            "task": "core.tasks.sync_contact_wallet_custom_fields",
            "crontab": every_10h_25,
            "queue": None,
            "description": "Sync wallet balance and location into GHL contact custom fields.",
        },
        {
            "name": "make-api-call-for-sync_numbers",
            "task": "sms_management_app.tasks.charge_due_transmit_numbers",
            "crontab": daily_midnight,
            "queue": None,
            "description": "Charge wallets for TransmitNumbers due for renewal today.",
        },
        {
            "name": "sync-client-owned-numbers",
            "task": "sms_management_app.tasks.sync_client_owned_numbers",
            "crontab": daily_0030,
            "queue": None,
            "description": "Sync owned TransmitSMS numbers for each active client account.",
        },
    ]

    for spec in task_specs:
        PeriodicTask.objects.update_or_create(
            name=spec["name"],
            defaults={
                "task": spec["task"],
                "crontab": spec["crontab"],
                "queue": spec["queue"],
                "enabled": True,
                "description": spec["description"],
            },
        )

    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


def unseed_celery_beat_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    PeriodicTask.objects.filter(name__in=TASK_NAMES).delete()
    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_wallettransaction_direction_segments"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(
            seed_celery_beat_periodic_tasks,
            unseed_celery_beat_periodic_tasks,
        ),
    ]
