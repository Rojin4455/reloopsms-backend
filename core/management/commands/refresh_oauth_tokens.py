"""
Run GHL OAuth refresh tasks synchronously (no worker required).

Use for deploy hooks, emergency token refresh, or cron outside Celery:

    python manage.py refresh_oauth_tokens
    python manage.py refresh_oauth_tokens --agency-only
    python manage.py refresh_oauth_tokens --locations-only
"""

from django.core.management.base import BaseCommand

from core.tasks import make_api_call, make_api_call_for_agency_token, make_api_call_for_company_token


class Command(BaseCommand):
    help = "Refresh GHL location and/or agency OAuth tokens immediately (runs in-process)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--agency-only",
            action="store_true",
            help="Only refresh AgencyToken rows",
        )
        parser.add_argument(
            "--company-only",
            action="store_true",
            help="Only refresh CompanyToken rows (location app GHL OAuth)",
        )
        parser.add_argument(
            "--locations-only",
            action="store_true",
            help="Only refresh GHLAuthCredentials (location) rows",
        )

    def handle(self, *args, **options):
        agency_only = options["agency_only"]
        company_only = options["company_only"]
        locations_only = options["locations_only"]

        if sum([agency_only, company_only, locations_only]) > 1:
            self.stderr.write(
                self.style.ERROR("Use at most one of --agency-only / --company-only / --locations-only")
            )
            return

        only_one = agency_only or company_only or locations_only
        refresh_locations = locations_only or not only_one
        refresh_company = company_only or not only_one
        refresh_agency = agency_only or not only_one

        if refresh_locations:
            self.stdout.write("Refreshing location tokens (GHLAuthCredentials)...")
            make_api_call.apply()
            self.stdout.write(self.style.SUCCESS("Location token refresh finished."))

        if refresh_company:
            self.stdout.write("Refreshing company tokens (CompanyToken)...")
            make_api_call_for_company_token.apply()
            self.stdout.write(self.style.SUCCESS("Company token refresh finished."))

        if refresh_agency:
            self.stdout.write("Refreshing agency tokens (AgencyToken)...")
            make_api_call_for_agency_token.apply()
            self.stdout.write(self.style.SUCCESS("Agency token refresh finished."))