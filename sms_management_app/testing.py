from sms_management_app.services import TransmitSMSService

from transmitsms.models import TransmitSMSAccount

# def get_numbers(account_id = None, filter_type=None):

#     service = TransmitSMSService()

#     if account_id:
#         transmit_sms = TransmitSMSAccount.objects.get(account_id=account_id)
#         available_numbers = service.get_dedicated_numbers(filter_type,api_key=transmit_sms.api_key, api_secret=transmit_sms.api_secret)

#     else:
#         available_numbers = service.get_dedicated_numbers(filter_type)
#     print("numbers:",available_numbers)


from decimal import Decimal
from core.models import TransmitNumber

def get_numbers(account_id=None, filter_type=None):
    service = TransmitSMSService()

    # Get numbers from TransmitSMS
    if account_id:
        transmit_sms = TransmitSMSAccount.objects.get(account_id=account_id)
        available_numbers = service.get_dedicated_numbers(
            filter_type,
            api_key=transmit_sms.api_key,
            api_secret=transmit_sms.api_secret
        )
        # ghl_account = transmit_sms.ghl_account  # assumes FK to GHLAuthCredentials
        ghl_account = transmit_sms.ghl_mapping.ghl_account

    else:
        available_numbers = service.get_dedicated_numbers(filter_type)
        ghl_account = None

    numbers_data = available_numbers.get("data", {}).get("numbers", [])
    api_numbers = {str(num["number"]): Decimal(str(num.get("price", 0))) for num in numbers_data}

    # # Existing numbers in DB for this account
    # existing_numbers = TransmitNumber.objects.filter(ghl_account=ghl_account)
    # existing_map = {n.number: n for n in existing_numbers}

    # to_create = []
    # to_update = []
    # to_delete = []

    # # ✅ 1. Update or create
    # for num_str, price in api_numbers.items():
    #     if num_str in existing_map:
    #         number_obj = existing_map[num_str]
    #         if number_obj.price != price:
    #             number_obj.price = price
    #             to_update.append(number_obj)
    #     else:
    #         to_create.append(TransmitNumber(
    #             ghl_account=ghl_account,
    #             number=num_str,
    #             price=price,
    #             status="available",
    #         ))

    # # ✅ 2. Delete numbers not present in API response
    # api_number_set = set(api_numbers.keys())
    # for num_str, num_obj in existing_map.items():
    #     if num_str not in api_number_set:
    #         to_delete.append(num_obj.id)

    # # ✅ Perform DB operations in bulk
    # if to_update:
    #     TransmitNumber.objects.bulk_update(to_update, ["price"])
    # if to_create:
    #     TransmitNumber.objects.bulk_create(to_create)
    # if to_delete:
    #     TransmitNumber.objects.filter(
    #         id__in=to_delete
    #     ).exclude(
    #         status__in=['registered', 'owned']
    #     ).delete()

    # print(f"✅ Synced TransmitSMS numbers:")
    # print(f"  Created: {len(to_create)}")
    # print(f"  Updated: {len(to_update)}")
    # print(f"  Deleted: {len(to_delete)}")

    # return {
    #     "created": len(to_create),
    #     "updated": len(to_update),
    #     "deleted": len(to_delete),
    #     "total": len(api_numbers),
    # }



def test_find_existing_account(name=None, email=None, phone=None):
    """
    Test function for finding an existing TransmitSMS account
    by name, email, or phone.
    """
    service = TransmitSMSService()
    result = service.find_existing_account(
        email=email,
        phone=phone,
        name=name
    )


    
    if result:
        print("[SUCCESS] Found account:")
        print(result)
    else:
        print("[INFO] No matching account found.")
    
    return result


def test_update_existing_account(client_id=None, email=None, phone=None, client_pays=None):
    service = TransmitSMSService()


    result = service.update_subaccount(
        client_pays=client_pays,
        client_id=client_id
    )
    
    if result:
        print("[SUCCESS] Found account:")
        print(result)
    else:
        print("[INFO] No matching account found.")
    
    return result



def get_all_numbers_account(client_id=None, email=None, phone=None, client_pays=None):
    service = TransmitSMSService()


    result = service.get_numbers()
    
    if result:
        print("[SUCCESS] Found account:")
        print(result)
    else:
        print("[INFO] No matching account found.")
    
    return result




