from django.db import migrations
from django.utils import timezone


TASK_NAME = "sweep-due-auto-recharges-every-5-minutes"


def seed_auto_recharge_sweep_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="*/5",
        hour="*",
        day_of_month="*",
        month_of_year="*",
        day_of_week="*",
        timezone="UTC",
    )

    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={
            "task": "core.tasks.sweep_due_auto_recharges_task",
            "crontab": crontab,
            "enabled": True,
            "description": "Enqueue overdue / stuck wallet auto-recharge retries.",
        },
    )

    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


def unseed_auto_recharge_sweep_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    PeriodicTask.objects.filter(name=TASK_NAME).delete()
    PeriodicTasks.objects.update_or_create(
        ident=1,
        defaults={"last_update": timezone.now()},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_walletautorecharge"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(
            seed_auto_recharge_sweep_task,
            unseed_auto_recharge_sweep_task,
        ),
    ]
