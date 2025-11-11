# tasks.py
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone
import time
import requests
import json
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

class GHLRateLimiter:
    """
    GoHighLevel API rate limiter
    - 10 requests per second
    - 200,000 requests per day (2 lakh)
    """
    REQUESTS_PER_SECOND = 10
    REQUESTS_PER_DAY = 200000
    
    @staticmethod
    def get_rate_limit_keys():
        """Get cache keys for rate limiting"""
        current_time = timezone.now()
        second_key = f"ghl_rate_limit_second_{current_time.strftime('%Y%m%d_%H%M%S')}"
        day_key = f"ghl_rate_limit_day_{current_time.strftime('%Y%m%d')}"
        return second_key, day_key
    
    @staticmethod
    def can_make_request():
        """Check if we can make a request within rate limits"""
        second_key, day_key = GHLRateLimiter.get_rate_limit_keys()
        
        # Check per-second limit
        current_second_count = cache.get(second_key, 0)
        if current_second_count >= GHLRateLimiter.REQUESTS_PER_SECOND:
            return False, "Per-second rate limit exceeded"
        
        # Check per-day limit
        current_day_count = cache.get(day_key, 0)
        if current_day_count >= GHLRateLimiter.REQUESTS_PER_DAY:
            return False, "Daily rate limit exceeded"
        
        return True, "OK"
    
    @staticmethod
    def increment_counters():
        """Increment rate limit counters"""
        second_key, day_key = GHLRateLimiter.get_rate_limit_keys()
        
        # Increment per-second counter (expires in 1 second)
        try:
            cache.add(second_key, 0, timeout=1)
            cache.incr(second_key)
        except ValueError:
            cache.set(second_key, 1, timeout=1)
        
        # Increment per-day counter (expires in 24 hours)
        try:
            cache.add(day_key, 0, timeout=86400)  # 24 hours
            cache.incr(day_key)
        except ValueError:
            cache.set(day_key, 1, timeout=86400)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def update_ghl_message_status_task(self, message_id, status, ghl_token, sms_message_id=None):
    """
    Celery task to update GHL message status with rate limiting
    """
    try:
        # Check rate limits
        can_proceed, reason = GHLRateLimiter.can_make_request()
        
        if not can_proceed:
            if "Per-second" in reason:
                # Retry after 1 second for per-second limit
                logger.warning(f"GHL per-second rate limit hit. Retrying in 1 second. Task: {self.request.id}")
                raise self.retry(countdown=1, exc=Exception(reason))
            elif "Daily" in reason:
                # Retry after 1 hour for daily limit
                logger.error(f"GHL daily rate limit hit. Retrying in 1 hour. Task: {self.request.id}")
                raise self.retry(countdown=3600, exc=Exception(reason))
        
        # Make the API call
        success = _make_ghl_api_call(message_id, status, ghl_token)
        
        if success:
            # Increment counters only on successful request
            GHLRateLimiter.increment_counters()
            logger.info(f"Successfully updated GHL message status for message_id: {message_id}")
            return {"status": "success", "message_id": message_id}
        else:
            # If API call failed, retry
            logger.warning(f"GHL API call failed for message_id: {message_id}. Retrying...")
            raise self.retry(countdown=3)
            
    except Exception as exc:
        logger.error(f"Error in update_ghl_message_status_task: {exc}")
        
        # Update local SMS message status if provided
        if sms_message_id:
            _update_local_sms_status(sms_message_id, f"GHL update failed: {str(exc)}")
        
        # Don't retry for certain errors
        if "Daily rate limit" in str(exc) and self.request.retries >= 3:
            logger.error(f"Giving up on GHL update after daily rate limit. Message ID: {message_id}")
            return {"status": "failed", "reason": "Daily rate limit exceeded", "message_id": message_id}
        
        raise self.retry(exc=exc)


def _make_ghl_api_call(message_id, status, ghl_token):
    """
    Make the actual API call to GHL using existing function
    """
    from sms_management_app.services import update_ghl_message_status  # Replace with your actual import path
    
    try:
        result = update_ghl_message_status(message_id, status, ghl_token)
        
        if result.get('success'):
            return True
        else:
            error_msg = result.get('error', 'Unknown error')
            
            # Check if it's a rate limit error
            if '429' in str(error_msg) or 'rate limit' in str(error_msg).lower():
                logger.warning(f"GHL rate limited for message_id: {message_id}")
                return False
            else:
                logger.error(f"GHL API error for message_id {message_id}: {error_msg}")
                return False
            
    except Exception as e:
        logger.error(f"Exception calling GHL API for message_id {message_id}: {e}")
        return False


def _update_local_sms_status(sms_message_id, error_message):
    """
    Update local SMS message with error information
    """
    try:
        from sms_management_app.models import SMSMessage  # Replace with your actual import
        sms_message = SMSMessage.objects.get(id=sms_message_id)
        sms_message.error_message = error_message
        sms_message.updated_at   = timezone.now()
        sms_message.save()
    except Exception as e:
        logger.error(f"Failed to update local SMS status: {e}")


@shared_task(bind=True, max_retries=3)
def batch_update_ghl_statuses(self, updates_batch):
    """
    Process a batch of GHL status updates
    """
    successful_updates = 0
    failed_updates = 0
    
    for update in updates_batch:
        try:
            # Queue individual update task
            update_ghl_message_status_task.delay(
                message_id=update['message_id'],
                status=update['status'],
                ghl_token=update['ghl_token'],
                sms_message_id=update.get('sms_message_id')
            )
            successful_updates += 1
        except Exception as e:
            logger.error(f"Failed to queue GHL update: {e}")
            failed_updates += 1
    
    return {
        "successful_queued": successful_updates,
        "failed_to_queue": failed_updates,
        "total": len(updates_batch)
    }


# Priority queue for urgent updates
@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def urgent_update_ghl_message_status(self, message_id, status, ghl_token, sms_message_id=None):
    """
    High-priority task for urgent GHL updates (e.g., delivery confirmations)
    """
    return update_ghl_message_status_task.apply(
        args=[message_id, status, ghl_token, sms_message_id],
        priority=9  # High priority
    )



from celery import shared_task
from django.utils import timezone
from django.core.exceptions import ValidationError
import requests
from core.models import Wallet
from sms_management_app.models import SMSMessage
import time

# enforce rate-limit per worker
@shared_task(rate_limit="9/s", bind=True, max_retries=3, default_retry_delay=5)
def process_sms_message(self, sms_id: str):
    """
    Push a queued inbound SMS message into GHL, respecting API rate limits (10 req/s).
    """
    try:
        print(f"🔎 [Task Start] Processing inbound SMS {sms_id}")

        sms = SMSMessage.objects.get(id=sms_id)
        wallet = Wallet.objects.get(account=sms.ghl_account)

        print(f"✅ Found SMS {sms.id} (direction={sms.direction}, status={sms.status})")
        print(f"💰 Wallet Balance Before: {wallet.balance}")

        if sms.direction != "inbound" or sms.status != "queued":
            msg = f"⏩ SMS {sms_id} skipped (not inbound or not queued)."
            print(msg)
            return msg

        # Charge wallet for inbound SMS
        try:
            cost, segments = wallet.charge_message(
                "inbound", sms.message_content, reference_id=sms.id
            )
            sms.cost = cost
            sms.segments = segments
            print(f"💸 Charged {cost} for inbound SMS ({segments} segments). New Balance={wallet.balance}")
        except ValidationError as e:
            # still no funds → keep queued
            sms.status = "queued"
            sms.save()
            print(f"⚠️ SMS {sms_id} re-queued (insufficient balance): {str(e)}")
            return f"SMS {sms_id} re-queued (insufficient balance)."

        # Push inbound message to GHL
        ghl_api_url = "https://services.leadconnectorhq.com/conversations/messages/inbound"
        payload = {
            "type": "SMS",
            "message": sms.message_content,
            "conversationId": sms.ghl_conversation_id,
            "conversationProviderId": "68dbda8a5f1dda4f65d4625f", #conversationProvidedId
        }
        headers = {
            "Authorization": f"Bearer {sms.ghl_account.access_token}",
            "Version": "2021-04-15",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        print(f"📤 Sending inbound SMS {sms.id} to GHL → Payload: {payload}")
        resp = requests.post(ghl_api_url, json=payload, headers=headers)
        data = resp.json()
        print(f"📥 GHL Response (status={resp.status_code}): {data}")

        if resp.status_code in (200, 201):
            sms.status = "delivered"
            sms.sent_at = timezone.now()
            print(f"✅ Inbound SMS {sms.id} delivered successfully.")
        else:
            # failed → refund and mark failed
            wallet.refund(
                sms.cost,
                reference_id=sms.id,
                description="Refund for failed inbound SMS"
            )
            sms.status = "failed"
            sms.error_message = data.get("error") or str(data)
            print(f"❌ Failed to deliver inbound SMS {sms.id}, refunded {sms.cost}. Error={sms.error_message}")

        sms.save()
        final_msg = f"Processed inbound SMS {sms_id} with status {sms.status}"
        print(f"🏁 [Task End] {final_msg}")
        return final_msg

    except SMSMessage.DoesNotExist:
        msg = f"⚠️ SMS {sms_id} not found"
        print(msg)
        return msg
    except Exception as e:
        print(f"🔥 Unexpected error in process_sms_message({sms_id}): {str(e)}")
        # retry if it looks transient (network/api issue)
        raise self.retry(exc=e)




from decimal import Decimal
from core.models import TransmitNumber
from sms_management_app.services import TransmitSMSService
from transmitsms.models import TransmitSMSAccount
from sms_management_app.models import GHLTransmitSMSMapping
from django.db import transaction
from dateutil import parser as date_parser

@shared_task
def sync_numbers(account_id=None, filter_type='available'):
    service = TransmitSMSService()

    # Get numbers from TransmitSMS
    if account_id:
        transmit_sms = TransmitSMSAccount.objects.get(account_id=account_id)
        available_numbers = service.get_dedicated_numbers(
            filter_type,
            api_key=transmit_sms.api_key,
            api_secret=transmit_sms.api_secret
        )
        ghl_account = transmit_sms.ghl_account  # assumes FK to GHLAuthCredentials
    else:
        available_numbers = service.get_dedicated_numbers(filter_type)
        ghl_account = None

    numbers_data = available_numbers.get("data", {}).get("numbers", [])
    api_numbers = {str(num["number"]): Decimal(str(num.get("price", 0))) for num in numbers_data}

    # Existing numbers in DB for this account
    existing_numbers = TransmitNumber.objects.filter(ghl_account=ghl_account)
    existing_map = {n.number: n for n in existing_numbers}

    to_create = []
    to_update = []
    to_delete = []

    # ✅ 1. Update or create
    for num_str, price in api_numbers.items():
        if num_str in existing_map:
            number_obj = existing_map[num_str]
            if number_obj.price != price:
                number_obj.price = price
                to_update.append(number_obj)
        else:
            to_create.append(TransmitNumber(
                ghl_account=ghl_account,
                number=num_str,
                price=price,
                status="available",
            ))

    # ✅ 2. Delete numbers not present in API response
    api_number_set = set(api_numbers.keys())
    for num_str, num_obj in existing_map.items():
        if num_str not in api_number_set:
            to_delete.append(num_obj.id)

    # ✅ Perform DB operations in bulk
    if to_update:
        TransmitNumber.objects.bulk_update(to_update, ["price"])
    if to_create:
        TransmitNumber.objects.bulk_create(to_create)
    if to_delete:
        TransmitNumber.objects.filter(
            id__in=to_delete
        ).exclude(
            status__in=['owned']
        ).delete()

    print(f"✅ Synced TransmitSMS numbers:")
    print(f"  Created: {len(to_create)}")
    print(f"  Updated: {len(to_update)}")
    print(f"  Deleted: {len(to_delete)}")

    return {
        "created": len(to_create),
        "updated": len(to_update),
        "deleted": len(to_delete),
        "total": len(api_numbers),
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_client_owned_numbers(self):
    """
    Periodically sync owned numbers for each active TransmitSMS client account.
    - For each client (TransmitSMSAccount with a mapping), fetch 'owned' numbers
    - For each number, fetch details via get_number
    - Update existing TransmitNumber or create new one
    - For new numbers, apply deduction logic based on subscription quota and price
    - Remove numbers from our DB that are no longer in TransmitSMS for that client
    """
    logger.info("[INFO] Starting owned numbers sync across clients")
    service = TransmitSMSService()

    # Iterate over active TransmitSMS accounts that are mapped to GHL accounts
    accounts = TransmitSMSAccount.objects.filter(is_active=True).select_related("ghl_mapping")
    processed = created_count = updated_count = deleted_count = 0

    for account in accounts:
        if not hasattr(account, "ghl_mapping"):
            continue
        ghl_account = account.ghl_mapping.ghl_account
        client_label = getattr(ghl_account, "location_name", getattr(account, "account_name", str(account.id)))
        try:
            logger.info(f"[INFO] Syncing numbers for client: {client_label}")
            resp = service.get_dedicated_numbers(filter_type="owned", api_key=account.api_key, api_secret=account.api_secret)
            if not resp.get("success"):
                logger.error(f"[ERROR] Failed to fetch owned numbers for client {client_label}: {resp.get('error')}")
                continue
            numbers = resp.get("data", {}).get("numbers", []) or []
            logger.debug(f"[DEBUG] Found {len(numbers)} numbers")

            # Get all numbers from API response (set of number strings)
            api_number_set = {str(item.get("number")) for item in numbers}

            # Get existing numbers in DB for this client
            existing_numbers = TransmitNumber.objects.filter(ghl_account=ghl_account, status__in=["owned", "pending"])
            existing_number_set = {tn.number for tn in existing_numbers}

            # Find numbers to delete (in DB but not in API response)
            numbers_to_delete = existing_number_set - api_number_set
            if numbers_to_delete:
                deleted_objs = TransmitNumber.objects.filter(
                    ghl_account=ghl_account,
                    number__in=numbers_to_delete,
                    status__in=["owned", "pending"]  # Only delete owned/pending, not registered
                )
                deleted_count_for_client = deleted_objs.count()
                deleted_objs.delete()
                deleted_count += deleted_count_for_client
                logger.info(f"[DELETE] Removed {deleted_count_for_client} numbers not found in TransmitSMS for client {client_label}: {list(numbers_to_delete)}")

            for item in numbers:
                processed += 1
                msisdn = str(item.get("number"))
                # Fetch number details for precise fields (price, auto_renew, status, next_charge)
                details = service.get_number(number=msisdn, api_key=account.api_key, api_secret=account.api_secret)
                if not details.get("success"):
                    logger.error(f"[ERROR] Failed details for {msisdn}: {details.get('error')}")
                    continue
                data = details.get("data", {})
                price = Decimal(str(data.get("price", item.get("price", 0) or 0)))
                status_raw = data.get("status") or item.get("status")
                # Map Transmit status to our status
                status = "owned" if (status_raw or "").lower() == "active" else "pending"
                next_charge_str = data.get("next_charge")
                next_renewal_date = None
                if next_charge_str:
                    try:
                        next_renewal_date = date_parser.parse(next_charge_str).date()
                    except Exception:
                        next_renewal_date = None

                # Upsert TransmitNumber
                try:
                    with transaction.atomic():
                        tn, created = TransmitNumber.objects.select_for_update().get_or_create(
                            number=msisdn,
                            defaults={
                                "ghl_account": ghl_account,
                                "price": price,
                                "status": status,
                                "is_active": True,
                                "next_renewal_date": next_renewal_date,
                                "monthly_charge": Decimal(str(price)),
                            },
                        )

                        if created:
                            # Determine standard vs premium and apply deduction logic if extra
                            is_standard = price <= Decimal("11")
                            use_wallet = False
                            charge_amount = Decimal("0.00")
                            wallet = getattr(ghl_account, "wallet", None)

                            if is_standard:
                                if ghl_account.can_purchase_standard():
                                    ghl_account.current_standard_purchased += 1
                                    ghl_account.save(update_fields=["current_standard_purchased"])
                                    tn.is_extra_number = False
                                else:
                                    use_wallet = True
                                    charge_amount = price
                                    if wallet and ghl_account.has_sufficient_wallet_balance(price):
                                        wallet.deduct_funds(
                                            amount=price,
                                            reference_id=str(tn.id),
                                            description=f"Sync purchase of extra standard number {msisdn}",
                                        )
                                        tn.is_extra_number = True
                                        tn.monthly_charge = price
                                    else:
                                        logger.warning(f"[WARN] Insufficient funds for extra standard number {msisdn} during sync")
                                        tn.is_extra_number = True
                                        tn.monthly_charge = price
                            else:
                                # Premium
                                if ghl_account.can_purchase_premium():
                                    ghl_account.current_premium_purchased += 1
                                    ghl_account.save(update_fields=["current_premium_purchased"])
                                    tn.is_extra_number = False
                                else:
                                    use_wallet = True
                                    charge_amount = price
                                    if wallet and ghl_account.has_sufficient_wallet_balance(price):
                                        wallet.deduct_funds(
                                            amount=price,
                                            reference_id=str(tn.id),
                                            description=f"Sync purchase of extra premium number {msisdn}",
                                        )
                                        tn.is_extra_number = True
                                        tn.monthly_charge = price
                                    else:
                                        logger.warning(f"[WARN] Insufficient funds for extra premium number {msisdn} during sync")
                                        tn.is_extra_number = True
                                        tn.monthly_charge = price

                            tn.next_renewal_date = next_renewal_date
                            tn.status = status
                            tn.price = price
                            tn.ghl_account = ghl_account
                            tn.save()
                            created_count += 1
                            logger.info(f"[SUCCESS] New number added: {msisdn}")
                        else:
                            # Update existing record
                            updated_fields = []
                            if tn.ghl_account_id != ghl_account.id:
                                tn.ghl_account = ghl_account
                                updated_fields.append("ghl_account")
                            if tn.price != price:
                                tn.price = price
                                updated_fields.append("price")
                            if tn.status != status:
                                tn.status = status
                                updated_fields.append("status")
                            if tn.next_renewal_date != next_renewal_date:
                                tn.next_renewal_date = next_renewal_date
                                updated_fields.append("next_renewal_date")
                            if updated_fields:
                                tn.save(update_fields=updated_fields + ["last_synced_at"])
                                updated_count += 1
                                logger.info(f"[UPDATE] Updated existing number: {msisdn}")
                except Exception as e:
                    logger.exception(f"[ERROR] Exception syncing number {msisdn} for {client_label}: {e}")
                    continue

        except Exception as e:
            logger.exception(f"[ERROR] Exception syncing client {client_label}: {e}")
            # Retry on transient issues
            continue

    logger.info(f"[INFO] Owned numbers sync complete. Processed={processed}, Created={created_count}, Updated={updated_count}, Deleted={deleted_count}")
    return {"processed": processed, "created": created_count, "updated": updated_count, "deleted": deleted_count}

from dateutil.relativedelta import relativedelta


@shared_task
def charge_due_transmit_numbers():
    """
    Charge wallet for TransmitNumbers whose next_renewal_date is today.
    Updates last_billed_at and next_renewal_date.
    """
    today = timezone.now().date()
    due_numbers = TransmitNumber.objects.filter(
        next_renewal_date=today,
        status="owned",
        is_extra_number=True,
        is_active=True
    )

    for number in due_numbers:
        wallet = getattr(number.ghl_account, "wallet", None)
        if not wallet:
            print(f"⚠️ No wallet found for account {number.ghl_account}")
            continue

        charge_amount = number.monthly_charge
        try:
            if wallet.balance >= charge_amount:
                # Deduct funds using your Wallet method
                wallet.deduct_funds(
                    amount=charge_amount,
                    reference_id=str(number.id),
                    description=f"Monthly renewal for Transmit number {number.number}"
                )

                # Update billing info
                number.last_billed_at = timezone.now()
                # Use relativedelta to safely add one month
                number.next_renewal_date += relativedelta(months=+1)
                number.save(update_fields=["last_billed_at", "next_renewal_date"])

                print(f"✅ Charged {number.number} for {charge_amount}, next renewal {number.next_renewal_date}")
            else:
                # Insufficient balance — optionally mark as inactive or notify user
                print(f"⚠️ Insufficient funds to charge {number.number} ({charge_amount} required)")
        except Exception as e:
            print(f"🔥 Failed to charge {number.number}: {e}")