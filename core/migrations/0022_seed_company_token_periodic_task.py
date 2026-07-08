from django.db import migrations
from django.utils import timezone


def seed_company_token_periodic_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="20",
        hour="*/10",
        day_of_month="*",
        month_of_year="*",
        day_of_week="*",
        timezone="UTC",
    )

    PeriodicTask.objects.update_or_create(
        name="make-api-call-for-company-every-6-hours",
        defaults={
            "task": "core.tasks.make_api_call_for_company_token",
            "crontab": crontab,
            "queue": "critical",
            "enabled": True,
            "description": "Refresh GHL company OAuth token and re-mint location tokens (critical queue).",
        },
    )

    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


def unseed_company_token_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    PeriodicTask.objects.filter(name="make-api-call-for-company-every-6-hours").delete()
    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_companytoken_alter_agencytoken_scope_and_more"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(
            seed_company_token_periodic_task,
            unseed_company_token_periodic_task,
        ),
    ]
