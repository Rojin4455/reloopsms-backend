import logging

from celery import shared_task
from core.ghl_auth import refresh_agency_token, refresh_location_token
from core.models import AgencyToken, GHLAuthCredentials

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=600, time_limit=660)
def make_api_call():
    """Refresh OAuth tokens for all GHL location credentials (one row at a time, errors isolated)."""
    for credentials in GHLAuthCredentials.objects.all():
        if refresh_location_token(credentials):
            logger.info("Refreshed location token for %s", credentials.location_id)


@shared_task(soft_time_limit=600, time_limit=660)
def make_api_call_for_agency_token():
    """Refresh OAuth tokens for all agency rows (errors isolated per row)."""
    for credentials in AgencyToken.objects.all():
        if refresh_agency_token(credentials):
            logger.info("Refreshed agency token for company %s", credentials.company_id)


from django.conf import settings
from core.models import Wallet
from core.service import GHLService

MAIN_LOCATION_ID = settings.GHL_MAIN_LOCATION_ID


@shared_task
def sync_all_wallets_with_ghl():
    # find the creds for main location
    try:
        main_creds = GHLAuthCredentials.objects.get(location_id=MAIN_LOCATION_ID)
    except GHLAuthCredentials.DoesNotExist:
        return

    service = GHLService(access_token=main_creds.access_token, auth_credentials=main_creds)

    for wallet in Wallet.objects.select_related("account").all():
        try:
          
           if wallet.ghl_object_id:
                print("type(wallet.cred_remaining)",type(wallet.cred_remaining))
                print("type(wallet.seg_remaining)",type(wallet.seg_remaining))
                print("type(wallet.cred_spent)",type(wallet.cred_spent))
                print("type(wallet.seg_used)",type(wallet.seg_used))
                payload = {
                    "cred_remaining": {
                        "currency": "default",
                        "value": float(wallet.cred_remaining)
                    },
                    "seg_remaining": int(wallet.seg_remaining),
                    "cred_spent": {
                        "currency": "default",
                        "value": float(wallet.cred_spent)
                    },
                    "seg_rates": {
                        "currency": "default",
                        "value": float(wallet.outbound_segment_charge)
                    },
                    "seg_used": int(wallet.seg_used),
                    "standard_numbers": wallet.account.current_standard_purchased,
                    "max_premium_numbers": wallet.account.max_premium_numbers,
                    "max_standard_numbers": wallet.account.max_standard_numbers,
                    "premium_numbers": wallet.account.current_premium_purchased,
                }
                update_response = service.update_record(wallet.ghl_object_id,MAIN_LOCATION_ID,payload)
                main_location = GHLAuthCredentials.objects.get(location_id=MAIN_LOCATION_ID)
                
                # After updating the record, update the contact's custom field if it exists
                print(f"\n===== Starting Contact Custom Field Update for Wallet {wallet.id} =====")
                try:
                    # Extract account_id from the update_record response
                    print(f"📋 Step 1: Extracting account_id from update_record response")
                    # Handle different response structures (could be directly the record or wrapped in "record")
                    record = update_response.get("record") if isinstance(update_response, dict) and "record" in update_response else update_response
                    props = record.get("properties", {}) if record and isinstance(record, dict) else {}
                    account_id = props.get("account_id")
                    print(f"📋 Step 1 Result: account_id = {account_id}")
                    
                    if account_id:
                        if main_location and main_location.access_token:
                            print(f"📋 Step 2: Creating GHLService with main location access token")
                            location_service = GHLService(
                                access_token=main_location.access_token,
                                auth_credentials=main_location,
                            )
                            
                            # Update the contact's custom field with cred_remaining value
                            CUSTOM_FIELD_ID = "32pWXPxvOxP5CGWZbaBZ"
                            print(f"📋 Step 3: Updating contact custom field")
                            print(f"📋 Contact ID: {account_id}")
                            print(f"📋 Custom Field ID: {CUSTOM_FIELD_ID}")
                            print(f"📋 Field Value: {wallet.cred_remaining} (type: {type(wallet.cred_remaining)})")
                            
                            location_service.update_contact_custom_field(
                                account_id,
                                CUSTOM_FIELD_ID,
                                f"{wallet.cred_remaining}"
                            )
                            print(f"✅ Step 3 Result: Successfully updated contact {account_id} custom field with cred_remaining: {wallet.cred_remaining}")
                        else:
                            print(f"⚠️ Step 2 Result: Missing main_location or access_token")
                    else:
                        print(f"⚠️ Step 1 Result: account_id is None or empty in update_record response")
                except Exception as contact_error:
                    # Log contact update error but don't fail the whole task
                    import traceback
                    print(f"❌ Error updating contact custom field: {contact_error}")
                    print(f"❌ Traceback: {traceback.format_exc()}")
                print(f"===== Finished Contact Custom Field Update for Wallet {wallet.id} =====\n")
        except Exception:
            # log exception and continue with next wallet
            continue


@shared_task(soft_time_limit=600, time_limit=660)
def sync_contact_wallet_custom_fields():
    """
    Sync wallet/contact data into GoHighLevel contact custom fields.

    Requires:
    - GHL_CF_CREDITS_REMAINING_NEW_ID in environment/settings
    - GHL_CF_SMS_RECHARGE_LOCATION_ID in environment/settings
    - GHLAuthCredentials.ghl_contact_id per account
    """
    credits_field_id = getattr(settings, "GHL_CF_CREDITS_REMAINING_NEW_ID", None)
    location_field_id = getattr(settings, "GHL_CF_SMS_RECHARGE_LOCATION_ID", None)

    if not credits_field_id or not location_field_id:
        logger.error(
            "Missing contact field IDs for sync task. "
            "Set GHL_CF_CREDITS_REMAINING_NEW_ID and GHL_CF_SMS_RECHARGE_LOCATION_ID."
        )
        return

    try:
        main_creds = GHLAuthCredentials.objects.get(location_id=MAIN_LOCATION_ID)
    except GHLAuthCredentials.DoesNotExist:
        logger.error("Main location credentials not found for location_id=%s", MAIN_LOCATION_ID)
        return

    if not main_creds.access_token:
        logger.error("Main location access token is missing for location_id=%s", MAIN_LOCATION_ID)
        return

    service = GHLService(access_token=main_creds.access_token, auth_credentials=main_creds)
    accounts = GHLAuthCredentials.objects.select_related("wallet").all()
    for account in accounts:
        try:
            wallet = getattr(account, "wallet", None)
            if not wallet:
                logger.info("Skipping location %s: no wallet", account.location_id)
                continue

            if not account.ghl_contact_id:
                logger.info("Skipping location %s: missing ghl_contact_id", account.location_id)
                continue

            if not account.location_id:
                logger.warning("Skipping account %s: missing location_id", account.pk)
                continue

            service.update_contact_custom_field(
                account.ghl_contact_id,
                credits_field_id,
                str(wallet.balance),
            )
            service.update_contact_custom_field(
                account.ghl_contact_id,
                location_field_id,
                account.location_id,
            )
            logger.info(
                "Synced contact custom fields for location %s contact %s",
                account.location_id,
                account.ghl_contact_id,
            )
        except Exception:
            logger.exception(
                "Failed syncing contact custom fields for location %s",
                account.location_id,
            )
