"""
Register the inbound webhook (forward_url) on already-leased TransmitSMS numbers.

TransmitSMS only delivers "Inbound SMS" — messages it cannot attribute to an
outbound send — to the number's forward_url. Numbers leased before that URL was
set receive nothing, so those messages are discarded by the provider. New leases
set it at purchase time; this backfills the existing ones.

Examples:

    # Show what would change, touching nothing (start here)
    python manage.py backfill_inbound_webhooks --dry-run

    # Apply to every mapped account
    python manage.py backfill_inbound_webhooks

    # Apply to one GHL location
    python manage.py backfill_inbound_webhooks --location-id gKnZUcMflBkB0OAHZiZe

Setting forward_url is idempotent, so this is safe to re-run — and it has to be,
because TransmitSMS does not report forward_url back. Neither get-numbers.json
nor get-number.json returns the field, so the only ways to confirm a URL is
registered are the TransmitSMS UI (Senders → number → Inbound Options) or a real
inbound message arriving.
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sms_management_app.inbound_routing import build_forward_url
from sms_management_app.models import GHLTransmitSMSMapping
from sms_management_app.services import TransmitSMSService

GET_NUMBERS_URL = "https://api.transmitsms.com/get-numbers.json"


class Command(BaseCommand):
    help = "Set forward_url (inbound webhook) on leased TransmitSMS numbers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--location-id",
            dest="location_id",
            help="Limit to a single GHL location_id",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report intended changes without calling the TransmitSMS API",
        )

    def handle(self, *args, **options):
        location_id = options.get("location_id")
        dry_run = options["dry_run"]

        # TransmitSMS has to be able to reach the URL, so a dev BASE_URL would
        # silently register unreachable webhooks on live numbers.
        base_url = (settings.BASE_URL or "").rstrip("/")
        if not dry_run and (
            not base_url.startswith("https://")
            or "localhost" in base_url
            or "127.0.0.1" in base_url
        ):
            raise CommandError(
                f"BASE_URL is {base_url!r} — run this on the server where BASE_URL is "
                "the public HTTPS host, or pass --dry-run."
            )

        mappings = GHLTransmitSMSMapping.objects.select_related(
            "ghl_account", "transmit_account"
        )
        if location_id:
            mappings = mappings.filter(ghl_account__location_id=location_id)
            if not mappings.exists():
                raise CommandError(f"No TransmitSMS mapping for location {location_id}")

        service = TransmitSMSService()
        updated = failed = 0

        for mapping in mappings:
            account = mapping.transmit_account
            label = mapping.ghl_account.location_name or mapping.ghl_account.location_id
            forward_url = build_forward_url(account.id)

            try:
                resp = requests.get(
                    GET_NUMBERS_URL,
                    auth=(account.api_key, account.api_secret),
                    timeout=30,
                )
                resp.raise_for_status()
                numbers = (resp.json() or {}).get("numbers") or []
            except (requests.RequestException, ValueError) as e:
                self.stderr.write(self.style.ERROR(f"{label}: could not list numbers — {e}"))
                failed += 1
                continue

            if not numbers:
                self.stdout.write(f"{label}: no leased numbers")
                continue

            for entry in numbers:
                number = entry.get("number")

                if dry_run:
                    self.stdout.write(f"{label} {number}: would set → {forward_url}")
                    updated += 1
                    continue

                result = service.edit_number_options(
                    number,
                    forward_url,
                    api_key=account.api_key,
                    api_secret=account.api_secret,
                )
                if result.get("success"):
                    self.stdout.write(self.style.SUCCESS(f"{label} {number}: set → {forward_url}"))
                    updated += 1
                else:
                    self.stderr.write(self.style.ERROR(f"{label} {number}: {result.get('error')}"))
                    failed += 1

        verb = "would set" if dry_run else "set"
        summary = f"Done — {verb} {updated}, failed {failed}"
        self.stdout.write(self.style.SUCCESS(summary) if not failed else self.style.WARNING(summary))
