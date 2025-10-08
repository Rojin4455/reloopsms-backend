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
