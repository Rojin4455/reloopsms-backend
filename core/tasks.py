import requests
from celery import shared_task
from core.models import GHLAuthCredentials, AgencyToken
from decouple import config

@shared_task
def make_api_call():
    tokens = GHLAuthCredentials.objects.all()

    for credentials in tokens:
    
        print("credentials tokenL", credentials)
        refresh_token = credentials.refresh_token

        
        response = requests.post('https://services.leadconnectorhq.com/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': config("GHL_CLIENT_ID"),
            'client_secret': config("GHL_CLIENT_SECRET"),
            'refresh_token': refresh_token
        })
        
        new_tokens = response.json()
        obj, created = GHLAuthCredentials.objects.update_or_create(
                location_id= new_tokens.get("locationId"),
                defaults={
                    "access_token": new_tokens.get("access_token"),
                    "refresh_token": new_tokens.get("refresh_token"),
                    "expires_in": new_tokens.get("expires_in"),
                    "scope": new_tokens.get("scope"),
                    "user_type": new_tokens.get("userType"),
                    "company_id": new_tokens.get("companyId"),
                    "user_id":new_tokens.get("userId"),

                }
            )
        print("refreshed: ", obj)


@shared_task
def make_api_call_for_agency_token():
    tokens = AgencyToken.objects.all()

    for credentials in tokens:
    
        print("credentials tokenL", credentials)
        refresh_token = credentials.refresh_token

        
        response = requests.post('https://services.leadconnectorhq.com/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': config("AGENCY_CLIENT_ID"),
            'client_secret': config("AGENCY_CLIENT_SECRET"),
            'refresh_token': refresh_token
        })
        
        response_data = response.json()
        obj, created = AgencyToken.objects.update_or_create(
            company_id=response_data.get("companyId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "user_id": response_data.get("userId"),
                "is_bulk_installation": response_data.get("isBulkInstallation", False),
                "token_type": response_data.get("token_type", "Bearer"),
                "refresh_token_id": response_data.get("refreshTokenId"),
            }
        )
        print("agency token refreshed: ", obj)
        
from django.db import transaction
from decimal import Decimal
from core.models import Wallet, WalletTransaction, GHLAuthCredentials
from core.service import GHLService
from django.conf import settings

MAIN_LOCATION_ID = "fM52tHdamVZya3QZH3ck"

@shared_task
def sync_all_wallets_with_ghl():
    # find the creds for main location
    try:
        main_creds = GHLAuthCredentials.objects.get(location_id=MAIN_LOCATION_ID)
    except GHLAuthCredentials.DoesNotExist:
        return

    service = GHLService(access_token=main_creds.access_token)

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
                            location_service = GHLService(access_token=main_location.access_token)
                            
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