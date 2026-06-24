"""
TransmitSMS balance helpers: fetch, refresh, pre-send checks, recharge warnings.
"""

from decimal import Decimal

from django.utils import timezone

from transmitsms.models import TransmitAgencyBalance, TransmitSMSAccount
from sms_management_app.models import GHLTransmitSMSMapping
from sms_management_app.services import TransmitSMSService


def get_transmit_sms_balance(
    *,
    transmit_account_id=None,
    location_id=None,
    api_key=None,
    api_secret=None,
):
    """
    Call TransmitSMS GET /get-balance.json and return a normalized result dict.

    Credential resolution (first match wins):
      1. Explicit api_key + api_secret
      2. transmit_account_id → TransmitSMSAccount row
      3. location_id → GHL mapping → TransmitSMSAccount
      4. Agency credentials from Django settings

    Returns:
        {
            "success": bool,
            "balance": Decimal|None,
            "currency": str|None,
            "account_label": str,
            "data": dict,
            "error": str (if failed),
        }
    """
    service = TransmitSMSService()
    account_label = "Agency (wholesale)"

    if api_key and api_secret:
        account_label = "Custom API credentials"
    elif transmit_account_id:
        account = TransmitSMSAccount.objects.filter(account_id=str(transmit_account_id)).first()
        if not account:
            return {
                "success": False,
                "error": f"No TransmitSMSAccount found for account_id={transmit_account_id}",
                "account_label": account_label,
            }
        api_key, api_secret = account.api_key, account.api_secret
        account_label = account.account_name or account.account_id
    elif location_id:
        mapping = (
            GHLTransmitSMSMapping.objects.select_related("transmit_account", "ghl_account")
            .filter(ghl_account__location_id=location_id)
            .first()
        )
        if not mapping:
            return {
                "success": False,
                "error": f"No GHL→Transmit mapping for location_id={location_id}",
                "account_label": account_label,
            }
        account = mapping.transmit_account
        api_key, api_secret = account.api_key, account.api_secret
        ghl_name = getattr(mapping.ghl_account, "location_name", None)
        account_label = ghl_name or account.account_name or account.account_id

    result = service.get_balance(api_key=api_key, api_secret=api_secret)
    result["account_label"] = account_label
    return result


def refresh_all_transmit_balances():
    """
    Fetch agency + all mapped subaccount balances from TransmitSMS and persist to DB.
    Manual only — triggered by admin refresh button.
    """
    service = TransmitSMSService()
    now = timezone.now()
    summary = {
        "agency": None,
        "accounts_updated": 0,
        "accounts_failed": 0,
        "errors": [],
    }

    agency_result = service.get_balance()
    if agency_result.get("success"):
        snapshot = TransmitAgencyBalance.get_snapshot()
        snapshot.balance = agency_result.get("balance") or Decimal("0")
        snapshot.currency = agency_result.get("currency") or "AUD"
        snapshot.synced_at = now
        snapshot.save()
        summary["agency"] = {
            "balance": str(snapshot.balance),
            "currency": snapshot.currency,
            "synced_at": snapshot.synced_at.isoformat() if snapshot.synced_at else None,
        }
    else:
        summary["errors"].append(
            {"scope": "agency", "error": agency_result.get("error", "Unknown error")}
        )

    for account in TransmitSMSAccount.objects.filter(is_active=True).order_by("account_name"):
        client_result = service.get_client(account.account_id)
        if not client_result.get("success"):
            summary["accounts_failed"] += 1
            summary["errors"].append(
                {
                    "scope": "account",
                    "account_id": account.account_id,
                    "account_name": account.account_name,
                    "error": client_result.get("error", "Unknown error"),
                }
            )
            continue

        if client_result.get("balance") is not None:
            account.balance = client_result["balance"]
        if client_result.get("currency"):
            account.currency = client_result["currency"]
        account.client_pays = bool(client_result.get("client_pays"))
        account.balance_synced_at = now
        account.save(
            update_fields=["balance", "currency", "client_pays", "balance_synced_at"]
        )
        summary["accounts_updated"] += 1

    return summary
