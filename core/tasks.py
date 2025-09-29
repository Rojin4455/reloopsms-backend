import requests
from celery import shared_task
from core.models import GHLAuthCredentials
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



        

from django.db import transaction
from decimal import Decimal
from core.models import Wallet, WalletTransaction, GHLAuthCredentials
from core.service import GHLService
from django.conf import settings

MAIN_LOCATION_ID = "fM52tHdamVZya3QZH3ck"

# @shared_task
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
                    "seg_used": int(wallet.seg_used),
                }                
                service.update_record(wallet.ghl_object_id,MAIN_LOCATION_ID,payload)
           pass
        except Exception:
            # log exception and continue with next wallet
            continue