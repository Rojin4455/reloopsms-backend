"""
Re-mint v2 location OAuth tokens for all GHL-installed sub-accounts.

Uses the stored CompanyToken (refreshed first) to call installedLocations +
locationToken, then upserts GHLAuthCredentials. Use this to upgrade legacy v1
location rows without running browser OAuth again.

Examples:
    python manage.py upgrade_ghl_location_tokens
    python manage.py upgrade_ghl_location_tokens --company-id 6d91N6e8GzmqEHW7qYov
    python manage.py upgrade_ghl_location_tokens --no-refresh-company-token
"""

from django.core.management.base import BaseCommand

from core.ghl_auth import remint_installed_location_tokens_for_company
from core.models import CompanyToken


class Command(BaseCommand):
    help = (
        "Re-mint GHL location tokens (v2 locationToken flow) for all installed "
        "sub-accounts under each CompanyToken row."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            help="Only upgrade locations for this GHL company_id",
        )
        parser.add_argument(
            "--no-refresh-company-token",
            action="store_true",
            help="Use the stored company access_token without refreshing it first",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        refresh_first = not options["no_refresh_company_token"]

        qs = CompanyToken.objects.all()
        if company_id:
            qs = qs.filter(company_id=company_id)

        if not qs.exists():
            self.stderr.write(self.style.ERROR("No CompanyToken rows found to upgrade."))
            return

        for company_token in qs:
            self.stdout.write(
                f"Upgrading installed location tokens for company {company_token.company_id}..."
            )
            try:
                summary = remint_installed_location_tokens_for_company(
                    company_token.company_id,
                    refresh_company_token_first=refresh_first,
                )
            except ValueError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    "company={company_id} installed={installed_count} "
                    "connected={connected_count} created={created_count} "
                    "updated={updated_count} skipped={skipped_count}".format(
                        company_id=summary["company_id"],
                        installed_count=summary["installed_count"],
                        connected_count=summary["connected_count"],
                        created_count=summary["created_count"],
                        updated_count=summary["updated_count"],
                        skipped_count=len(summary["skipped_locations"]),
                    )
                )
            )
            if summary["skipped_locations"]:
                for item in summary["skipped_locations"]:
                    self.stdout.write(
                        f"  skipped {item.get('location_id')}: {item.get('reason')}"
                    )
