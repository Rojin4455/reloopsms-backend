"""
Run GHL OAuth refresh tasks synchronously (no worker required).

Use for deploy hooks, emergency token refresh, or cron outside Celery:

    python manage.py refresh_oauth_tokens
    python manage.py refresh_oauth_tokens --agency-only
    python manage.py refresh_oauth_tokens --locations-only
"""

from django.core.management.base import BaseCommand

from core.tasks import make_api_call, make_api_call_for_agency_token


class Command(BaseCommand):
    help = "Refresh GHL location and/or agency OAuth tokens immediately (runs in-process)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--agency-only",
            action="store_true",
            help="Only refresh AgencyToken rows",
        )
        parser.add_argument(
            "--locations-only",
            action="store_true",
            help="Only refresh GHLAuthCredentials (location) rows",
        )

    def handle(self, *args, **options):
        agency_only = options["agency_only"]
        locations_only = options["locations_only"]

        if agency_only and locations_only:
            self.stderr.write(self.style.ERROR("Use at most one of --agency-only / --locations-only"))
            return

        if not agency_only:
            self.stdout.write("Refreshing location tokens (GHLAuthCredentials)...")
            make_api_call.apply()
            self.stdout.write(self.style.SUCCESS("Location token refresh finished."))

        if not locations_only:
            self.stdout.write("Refreshing agency tokens (AgencyToken)...")
            make_api_call_for_agency_token.apply()
            self.stdout.write(self.style.SUCCESS("Agency token refresh finished."))
