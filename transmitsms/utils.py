"""
Utilities for TransmitSMS MMS webhook setup.
"""
import requests
from django.conf import settings

from core.models import GHLAuthCredentials
from sms_management_app.models import GHLTransmitSMSMapping
from .models import TransmitSMSMMSWebhook


TRANSMIT_MMS_WEBHOOK_API = "https://api.transmitmessage.com/v2/webhook"
WEBHOOK_EVENT_TYPES = ["MMS_STATUS", "MMS_INBOUND"]


def create_mms_webhooks_for_locations(location_ids: list, *, update_existing: bool = False):
    """
    Create TransmitSMS MMS webhooks for each location and save to DB.

    For each location_id:
    1. Resolve GHL account → TransmitSMS account via mapping
    2. Create webhook via TransmitSMS API (MMS_STATUS + MMS_INBOUND)
    3. Save TransmitSMSMMSWebhook record

    Args:
        location_ids: List of GHL location IDs (e.g. ["HyePrNbBkAifLYSjtIAk", ...])
        update_existing: If True, recreate webhook and update DB when config already exists.
                        If False, skip locations that already have MMS webhook configured.

    Returns:
        list: Results per location:
              [{"location_id": str, "success": bool, "webhook_id": str|None, "error": str|None}, ...]
    """
    base_url = (settings.BASE_URL or "").rstrip("/")
    callback_url = f"{base_url}/api/transmit-sms/dlr-callback/"

    results = []
    for location_id in location_ids:
        try:
            ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            results.append({
                "location_id": location_id,
                "success": False,
                "webhook_id": None,
                "error": "GHL account not found",
            })
            continue

        try:
            mapping = GHLTransmitSMSMapping.objects.select_related("transmit_account").get(
                ghl_account=ghl_account
            )
        except GHLTransmitSMSMapping.DoesNotExist:
            results.append({
                "location_id": location_id,
                "success": False,
                "webhook_id": None,
                "error": "No TransmitSMS account mapped for this location",
            })
            continue

        transmit_account = mapping.transmit_account

        # Skip if already configured (unless update_existing)
        existing = TransmitSMSMMSWebhook.objects.filter(transmit_account=transmit_account).first()
        if existing and not update_existing:
            results.append({
                "location_id": location_id,
                "success": True,
                "webhook_id": existing.webhook_id,
                "error": None,
                "skipped": "Already configured",
            })
            continue

        # Create webhook via TransmitSMS API
        webhook_name = f"MMS Webhook - {ghl_account.location_name or location_id}"
        payload = {
            "name": webhook_name[:255],
            "url": callback_url,
            "filter": {"event_type": WEBHOOK_EVENT_TYPES},
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": transmit_account.api_key,
        }

        try:
            resp = requests.post(TRANSMIT_MMS_WEBHOOK_API, json=payload, headers=headers, timeout=30)
        except requests.RequestException as e:
            results.append({
                "location_id": location_id,
                "success": False,
                "webhook_id": None,
                "error": str(e),
            })
            continue

        data = resp.json() if resp.text else {}
        if resp.status_code not in (200, 201):
            err = data.get("error") or data.get("message") or resp.text
            results.append({
                "location_id": location_id,
                "success": False,
                "webhook_id": None,
                "error": err or f"HTTP {resp.status_code}",
            })
            continue

        webhook_id = data.get("id")
        if not webhook_id:
            results.append({
                "location_id": location_id,
                "success": False,
                "webhook_id": None,
                "error": "No webhook id in API response",
            })
            continue

        TransmitSMSMMSWebhook.objects.update_or_create(
            transmit_account=transmit_account,
            defaults={
                "webhook_id": webhook_id,
                "webhook_name": webhook_name[:255],
            },
        )

        results.append({
            "location_id": location_id,
            "success": True,
            "webhook_id": webhook_id,
            "error": None,
        })

    return results
