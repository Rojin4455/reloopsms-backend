"""
Fetch TransmitSMS wallet balance on demand.

Examples:

    # Agency wholesale balance (default)
    python manage.py get_transmit_balance

    # Balance for a GHL location's mapped Transmit subaccount
    python manage.py get_transmit_balance --location-id gKnZUcMflBkB0OAHZiZe

    # Balance for a Transmit client id
    python manage.py get_transmit_balance --transmit-account-id 197820

    # Raw JSON from the API
    python manage.py get_transmit_balance --json
"""

import json

from django.core.management.base import BaseCommand

from sms_management_app.transmit_balance import get_transmit_sms_balance


class Command(BaseCommand):
    help = "Fetch TransmitSMS account balance (agency or subaccount)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--location-id",
            dest="location_id",
            help="GHL location_id — uses that location's mapped Transmit subaccount",
        )
        parser.add_argument(
            "--transmit-account-id",
            dest="transmit_account_id",
            help="TransmitSMS client account_id (subaccount)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print full API response JSON",
        )

    def handle(self, *args, **options):
        result = get_transmit_sms_balance(
            location_id=options.get("location_id"),
            transmit_account_id=options.get("transmit_account_id"),
        )

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, default=str))
            if not result.get("success"):
                self.stderr.write(self.style.ERROR(result.get("error", "Request failed")))
            return

        label = result.get("account_label", "Account")
        if not result.get("success"):
            self.stderr.write(self.style.ERROR(f"{label}: {result.get('error', 'Unknown error')}"))
            if result.get("response_text"):
                self.stderr.write(result["response_text"])
            return

        balance = result.get("balance")
        currency = result.get("currency") or "AUD"
        self.stdout.write(self.style.SUCCESS(f"{label}"))
        self.stdout.write(f"  Balance: {balance} {currency}")
