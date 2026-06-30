"""
Exchange a GHL OAuth authorization code for location tokens (CLI fallback).

Use when the browser OAuth loop fails but you captured ?code=... from the redirect URL:

    python manage.py exchange_ghl_auth_code --code "YOUR_CODE_HERE"

Or print the correct authorize URL to open manually:

    python manage.py exchange_ghl_auth_code --print-url
"""

from django.core.management.base import BaseCommand

from core.ghl_auth import build_location_oauth_url, exchange_location_oauth_code


class Command(BaseCommand):
    help = "Exchange a GHL OAuth code for location tokens, or print the authorize URL."

    def add_arguments(self, parser):
        parser.add_argument("--code", help="Authorization code from GHL redirect (?code=...)")
        parser.add_argument(
            "--print-url",
            action="store_true",
            help="Print the OAuth URL to open in a browser (use incognito if login loops)",
        )

    def handle(self, *args, **options):
        if options["print_url"]:
            url = build_location_oauth_url()
            self.stdout.write("Open this URL in an incognito/private window:\n")
            self.stdout.write(url)
            return

        code = options.get("code")
        if not code:
            self.stderr.write(self.style.ERROR("Provide --code or use --print-url"))
            return

        result, error = exchange_location_oauth_code(code)
        if error:
            self.stderr.write(self.style.ERROR(f"Token exchange failed: {error}"))
            return

        obj, created = result
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} tokens for {obj.location_name} ({obj.location_id})"
            )
        )
