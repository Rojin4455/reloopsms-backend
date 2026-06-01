"""
Watchdog for django-celery-beat periodic tasks.

If Beat stops scheduling (process crash, stale file scheduler, etc.), this command
detects overdue tasks and re-queues them via the Celery broker.

Run hourly from cron on the server:

    0 * * * * cd /path/to/reloopsms-backend && /path/to/venv/bin/python manage.py ensure_periodic_tasks

Dry-run (report only):

    python manage.py ensure_periodic_tasks --dry-run
"""

from datetime import timedelta

from celery import current_app
from django.core.management.base import BaseCommand
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

# Slightly longer than the configured interval to avoid false positives.
TASK_MAX_AGE = {
    "make-api-call-every-6-hours": timedelta(hours=11),
    "make-api-call-for-agency-every-6-hours": timedelta(hours=11),
    "sync-contact-wallet-custom-fields-every-10-hours": timedelta(hours=11),
    "make-api-call-for-sync_numbers": timedelta(hours=25),
    "sync-client-owned-numbers": timedelta(hours=25),
}

DEFAULT_MAX_AGE = timedelta(hours=25)


class Command(BaseCommand):
    help = "Re-queue Celery Beat periodic tasks that have not run within their expected window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report overdue tasks without enqueueing them.",
        )
        parser.add_argument(
            "--fail-on-overdue",
            action="store_true",
            help="Exit with code 1 when any task is overdue (for monitoring hooks).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        fail_on_overdue = options["fail_on_overdue"]
        now = timezone.now()
        overdue = []

        for periodic_task in PeriodicTask.objects.filter(enabled=True).order_by("name"):
            max_age = TASK_MAX_AGE.get(periodic_task.name, DEFAULT_MAX_AGE)
            last_run = periodic_task.last_run_at

            if last_run is None:
                if periodic_task.total_run_count == 0 and (now - periodic_task.date_changed) < max_age:
                    self.stdout.write(
                        f"OK {periodic_task.name}: waiting for first Beat run "
                        f"(enabled {periodic_task.date_changed:%Y-%m-%d %H:%M UTC})"
                    )
                    continue
                overdue.append(periodic_task)
                continue

            age = now - last_run
            if age <= max_age:
                self.stdout.write(
                    f"OK {periodic_task.name}: last run {last_run:%Y-%m-%d %H:%M UTC} "
                    f"({age.total_seconds() / 3600:.1f}h ago)"
                )
                continue

            overdue.append(periodic_task)

        if not overdue:
            self.stdout.write(self.style.SUCCESS("All enabled periodic tasks are within schedule."))
            return

        for periodic_task in overdue:
            last_run_display = (
                periodic_task.last_run_at.strftime("%Y-%m-%d %H:%M UTC")
                if periodic_task.last_run_at
                else "never"
            )
            max_hours = TASK_MAX_AGE.get(periodic_task.name, DEFAULT_MAX_AGE).total_seconds() / 3600
            message = (
                f"OVERDUE {periodic_task.name}: last run {last_run_display} "
                f"(max {max_hours:.0f}h) -> task {periodic_task.task}"
            )

            if dry_run:
                self.stdout.write(self.style.WARNING(f"{message} [dry-run, not enqueued]"))
                continue

            send_kwargs = {}
            if periodic_task.queue:
                send_kwargs["queue"] = periodic_task.queue

            result = current_app.send_task(periodic_task.task, **send_kwargs)
            self.stdout.write(self.style.WARNING(f"{message} -> re-queued as {result.id}"))

        if fail_on_overdue:
            raise SystemExit(1)
