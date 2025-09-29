from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import GHLAuthCredentials, Wallet

# @receiver(post_save, sender=GHLAuthCredentials)
# def create_wallet_for_ghl_account(sender, instance, created, **kwargs):
#     if created:
#         Wallet.objects.create(account=instance)
#         print(f"💰 Wallet created for new GHL account {instance.user_id}")



from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from decimal import Decimal
from core.models import GHLAuthCredentials, Wallet, WalletTransaction
from core.service import GHLService
from django.conf import settings
from decimal import Decimal, InvalidOperation


MAIN_LOCATION_ID = "fM52tHdamVZya3QZH3ck"  # your main location

@receiver(post_save, sender=GHLAuthCredentials)
def sync_wallet_with_ghl(sender, instance, created, **kwargs):
    if not created:
        return

# def sync_wallet_with_ghl():
#     instance = GHLAuthCredentials.objects.get(location_id='Yx7Y0yVjvSx8tJ5RZoyG')
#     # 1) get main location creds (the integration account that has access to the main custom objects)
    try:
        main_creds = GHLAuthCredentials.objects.get(location_id=MAIN_LOCATION_ID)
    except GHLAuthCredentials.DoesNotExist:
        # You may want to log/error notify here
        return

    ghl = GHLService(access_token=main_creds.access_token)

    # 2) search for records on main location
    try:
        search = ghl.search_records(MAIN_LOCATION_ID, page=1, page_limit=50)
    except Exception as e:
        # log error
        return

    found_record = None
    found_record_id = None
    for rec in search.get("records", []):
        props = rec.get("properties", {})
        # account_id may be company_name or user_id depending on how you pre-created the object
        if props.get("locationid") == instance.location_id or props.get("account_id") == instance.location_name or props.get("business_name") == instance.location_name:
            found_record = rec
            found_record_id = rec.get("id")
            break

    def extract_values_from_props(props):
        # defensive extraction
        print("propsss: ", props)
        def safe_val(key, default="0.00"):
            v = props.get(key)

            # Handle dict with {"value": ...}
            if isinstance(v, dict) and "value" in v:
                val = v["value"]
            else:
                val = v

            # If it's None or empty, return default
            if val is None or val == "":
                return Decimal(default)

            # Try converting safely
            try:
                return Decimal(str(val))
            except (InvalidOperation, TypeError, ValueError):
                return Decimal(default)

        # base values
        # cred_purchased = safe_val("cred_purchased")
        seg_rate = safe_val("seg_rates") or Decimal("0.074")
        # cred_spent = safe_val("cred_spent")
        cred_remaining = safe_val("cred_remaining")
        # seg_purchased = int(props.get("seg_purchased") or 0)
        seg_remaining = int(props.get("seg_remaining") or 0)
        # seg_used = int(props.get("seg_used") or 0)
        

        # ---- FIX / RECALCULATE ----
        # credit spent
        # if cred_spent < 0:
        #     cred_spent = Decimal("0.00")

        # credit remaining
        # if cred_remaining <= 0:
        #     cred_remaining = cred_purchased - cred_spent
        #     if cred_remaining < 0:
        #         cred_remaining = Decimal("0.00")
        # else:
        #     cred_remaining+=cred_purchased
        #     if cred_remaining < 0:
        #         cred_remaining = Decimal("0.00")

        # segment remaining
        if seg_rate > 0:
            calc_seg_remaining = int(cred_remaining / seg_rate)
        else:
            calc_seg_remaining = 0
        if seg_remaining <= 0 or seg_remaining != calc_seg_remaining:
            seg_remaining = calc_seg_remaining

        # segment used
        # if seg_purchased > 0:
        #     seg_used = seg_purchased - seg_remaining
        #     if seg_used < 0:
        #         seg_used = 0
        # else:
        #     seg_used = 0

        # segment purchased
        # if seg_purchased <= 0:
        #     seg_purchased = seg_used + seg_remaining

        return {
            # "cred_purchased": cred_purchased,
            # "cred_spent": cred_spent,
            "cred_remaining": cred_remaining,
            # "seg_purchased": seg_purchased,
            "seg_remaining": seg_remaining,
            # "seg_used": seg_used,
            "seg_rate":seg_rate,
            "business_name": props.get("business_name") or instance.location_name,
            "contact": props.get("contact") or instance.contact_name,
            "locationid":instance.location_id
        }

    if found_record:
        props = found_record.get("properties", {})
        vals = extract_values_from_props(props)
        # create wallet and transaction for initial credited amount (if present)
        with transaction.atomic():
            wallet, created_wallet = Wallet.objects.get_or_create(account=instance, defaults={
                "ghl_object_id": found_record_id,
                # "cred_purchased": vals["cred_purchased"],
                # "cred_spent": vals["cred_spent"],
                "cred_remaining": vals["cred_remaining"],
                # "seg_purchased": vals["seg_purchased"],
                "seg_remaining": vals["seg_remaining"],
                # "seg_used": vals["seg_used"],
                "business_name": vals["business_name"],
                "contact": vals["contact"],
                "balance": vals["cred_remaining"],
                "outbound_segment_charge":vals["seg_rate"]
            })

            if not created_wallet:
                # update existing wallet fields
                wallet.ghl_object_id = found_record_id
                # wallet.cred_purchased = vals["cred_purchased"]
                # wallet.cred_spent = vals["cred_spent"]
                wallet.cred_remaining = vals["cred_remaining"]
                # wallet.seg_purchased = vals["seg_purchased"]
                wallet.seg_remaining = vals["seg_remaining"]
                # wallet.seg_used = vals["seg_used"]
                wallet.business_name = vals["business_name"]
                wallet.contact = vals["contact"]
                # update DB balance to match GHL cred_remaining
                wallet.balance = vals["cred_remaining"]
                wallet.outbound_segment_charge=vals["seg_rate"]
                wallet.save()

            # create an initial transaction if wallet was just created and has balance > 0
            if created_wallet and vals["cred_remaining"] > 0:
                WalletTransaction.objects.create(
                    wallet=wallet,
                    transaction_type="credit",
                    amount=vals["cred_remaining"],
                    balance_after=wallet.balance,
                    description="Initial credit from GHL object on onboarding",
                    reference_id=found_record_id
                )

            vals["seg_rates"] = vals["seg_rate"]
            del vals["seg_rate"]
            print("vals",vals)
            payload = format_for_ghl(vals)
            created_obj = ghl.update_record(found_record_id,MAIN_LOCATION_ID, payload)
        return

    # Not found: create a new custom object in MAIN location with zero credits
    create_props = {
        "account_id": instance.location_name or instance.user_id,
        "cred_purchased": {"currency": "default", "value": 0},
        "seg_rates": {"currency": "default", "value": float(getattr(instance, "outbound_segment_charge", 0.074))},
        "seg_purchased": 0,
        "seg_remaining": 0,
        "cred_spent": {"currency": "default", "value": 0},
        "cred_remaining": {"currency": "default", "value": 0},
        "business_name": instance.location_name or "Unknown",
        "contact": instance.contact_name or "",
        "locationid": instance.location_id,
    }

    try:
        created_obj = ghl.create_record(MAIN_LOCATION_ID, create_props)
        print("created_obj",created_obj)
        record=created_obj.get("record",{})
        props = record.get("properties", {})

        def safe_decimal(val, default=Decimal("0.00")):
            """Convert to Decimal safely"""
            if isinstance(val, dict) and "value" in val:
                return Decimal(str(val["value"]))
            try:
                return Decimal(str(val))
            except Exception:
                return default

        seg_rate = safe_decimal(props.get("seg_rates", {}).get("value", 0.074))
        business_name = props.get("business_name") or instance.location_name or "Unknown"
        contact = props.get("contact") or instance.contact_name or ""
        rec_id = record.get("id")
        seg_rate = created_obj.get("record",{}).get("properties").get("seg_rates").get("value")
        
    except Exception:
        rec_id = None

    # create wallet with zero initial credits
    with transaction.atomic():
        wallet, created_wallet = Wallet.objects.get_or_create(account=instance, defaults={
            "ghl_object_id": rec_id,
            "cred_purchased": Decimal("0.00"),
            "cred_spent": Decimal("0.00"),
            "cred_remaining": Decimal("0.00"),
            "seg_purchased": 0,
            "seg_remaining": 0,
            "seg_used": 0,
            "outbound_segment_charge":seg_rate,
            "business_name": business_name,
            "contact": contact,
            "balance": Decimal("0.00")
        })


def format_for_ghl(vals: dict) -> dict:
    """Convert vals dict into GHL API-compliant payload"""
    currency_fields = ["cred_purchased", "cred_spent", "cred_remaining", "seg_rates"]

    properties = {}
    for key, value in vals.items():
        # Wrap currency fields
        if key in currency_fields:
            properties[key] = {
                "currency": "default",
                "value": float(value) if isinstance(value, Decimal) else value
            }
        else:
            # Convert Decimals to float
            properties[key] = float(value) if isinstance(value, Decimal) else value

    return properties