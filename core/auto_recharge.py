"""
Wallet auto-recharge: Stripe charge + retry state + GHL event notify.

Entry points:
  - handle_create_deduction_request(...)  (GHL inbound webhook)
  - process_auto_recharge_retry(recharge_id)  (Celery delayed / sweeper)
  - sweep_due_auto_recharges()  (Celery beat safety net)
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

import stripe
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    GHLAuthCredentials,
    StripeCustomerData,
    Wallet,
    WalletAutoRecharge,
    WalletTransaction,
)

logger = logging.getLogger(__name__)

SKIP_BALANCE_THRESHOLD = Decimal("5.00")
DEFAULT_MAX_ATTEMPTS = 2

STRIPE_RECHARGE_AMOUNT_MAP = {
    Decimal("31.00"): Decimal("30.00"),
    Decimal("51.00"): Decimal("50.00"),
    Decimal("101.90"): Decimal("100.00"),
    Decimal("203.80"): Decimal("200.00"),
    Decimal("509.50"): Decimal("500.00"),
    Decimal("1019.00"): Decimal("1000.00"),
}

REQUIRES_ACTION_CODES = {
    "expired_card",
    "lost_card",
    "stolen_card",
    "incorrect_cvc",
    "incorrect_number",
    "invalid_expiry_month",
    "invalid_expiry_year",
    "invalid_cvc",
    "card_not_supported",
    "currency_not_supported",
    "authentication_required",
    "card_decline_rate_limit_exceeded",
}

REQUIRES_ACTION_DECLINE_CODES = {
    "stolen_card",
    "lost_card",
    "pickup_card",
    "restricted_card",
    "security_violation",
    "revocation_of_authorization",
    "card_not_supported",
    "do_not_honor",  # often permanent; treat as action if repeated — first pass: retryable via code
}


def _ensure_stripe_api_key():
    stripe.api_key = (
        settings.STRIPE_TEST_API_KEY if settings.DEBUG else settings.STRIPE_LIVE_API_KEY
    )


def parse_recharge_charge_and_credit(recharge_text: str):
    """
    Parse GHL "SMS Credit Recharge" text like "$30.00 Credit + $1 Card Fee".
    Returns (charge_amount, credit_amount, error_message).
    """
    amounts = re.findall(r"\$([\d\.]+)", recharge_text or "")
    if not amounts:
        return None, None, "Could not parse dollar amounts from SMS Credit Recharge"

    charge_amount = sum(Decimal(a) for a in amounts).quantize(Decimal("0.01"))
    credit_amount = STRIPE_RECHARGE_AMOUNT_MAP.get(charge_amount)
    if credit_amount is None:
        credit_amount = Decimal(amounts[0]).quantize(Decimal("0.01"))
        if credit_amount <= 0 or charge_amount < credit_amount:
            return None, None, f"Unknown or invalid recharge amount: {charge_amount}"

    return charge_amount, credit_amount, None


def extract_create_deduction_params(data):
    if not isinstance(data, dict):
        return {"location_id": "", "recharge_text": ""}

    custom = data.get("customData") or data.get("custom_data") or {}
    if not isinstance(custom, dict):
        custom = {}

    location_id = (
        data.get("SMS Recharge LocationID")
        or custom.get("SMS Recharge LocationID")
        or ""
    )
    # GHL sends the contact field at the top level even when it is blank,
    # and the workflow value under customData. A blank top-level string
    # must not hide the customData amount.
    recharge_text = (
        data.get("SMS Credit Recharge")
        or custom.get("SMS Credit Recharge")
        or ""
    )

    return {
        "location_id": str(location_id).strip(),
        "recharge_text": recharge_text,
    }


def lookup_latest_stripe_customer_id(email: str) -> Optional[str]:
    _ensure_stripe_api_key()
    customers = stripe.Customer.search(
        query=f"email:'{email}'",
        limit=10,
    )
    if not customers.data:
        return None
    latest_customer = sorted(customers.data, key=lambda c: c.created, reverse=True)[0]
    return latest_customer.id


def lookup_stripe_card_payment_method_id(customer_id: str) -> Optional[str]:
    _ensure_stripe_api_key()
    try:
        payment_methods = stripe.PaymentMethod.list(
            customer=customer_id,
            type="card",
            limit=1,
        )
        if payment_methods.data:
            return payment_methods.data[0].id
    except Exception as e:
        logger.warning("Failed to list Stripe payment methods for %s: %s", customer_id, e)
    return None


def classify_stripe_failure(
    *,
    code: Optional[str] = None,
    decline_code: Optional[str] = None,
    message: Optional[str] = None,
) -> str:
    code = (code or "").strip().lower()
    decline_code = (decline_code or "").strip().lower()

    if code in REQUIRES_ACTION_CODES:
        return WalletAutoRecharge.FAILURE_REQUIRES_ACTION
    if decline_code in REQUIRES_ACTION_DECLINE_CODES - {"do_not_honor"}:
        return WalletAutoRecharge.FAILURE_REQUIRES_ACTION
    if code or decline_code or message:
        return WalletAutoRecharge.FAILURE_RETRYABLE
    return WalletAutoRecharge.FAILURE_INTERNAL


def _retry_delay_seconds() -> int:
    return int(getattr(settings, "AUTO_RECHARGE_RETRY_DELAY_SECONDS", 7200))


def _cooldown_seconds() -> int:
    return int(getattr(settings, "AUTO_RECHARGE_COOLDOWN_SECONDS", 86400))


def _build_event_payload(recharge: WalletAutoRecharge, event: str, severity: str, **extra) -> dict:
    wallet_balance = None
    try:
        wallet = getattr(recharge.account, "wallet", None)
        if wallet is not None:
            wallet_balance = float(wallet.balance)
    except Exception:
        pass

    payload = {
        "event": event,
        "severity": severity,
        "location_id": recharge.location_id,
        "location_name": recharge.account.location_name,
        "ghl_contact_email": recharge.ghl_contact_email or recharge.account.ghl_contact_email,
        "ghl_contact_id": recharge.ghl_contact_id or recharge.account.ghl_contact_id,
        "wallet_balance": wallet_balance,
        "attempt": recharge.attempt_count,
        "max_attempts": recharge.max_attempts,
        "charge_amount": float(recharge.charge_amount),
        "credit_amount": float(recharge.credit_amount),
        "recharge_id": str(recharge.id),
        "occurred_at": timezone.now().isoformat(),
    }
    payload.update(extra)
    return payload


def queue_ghl_auto_recharge_event(payload: dict):
    from core.tasks import notify_ghl_auto_recharge_event_task

    notify_ghl_auto_recharge_event_task.delay(payload)


def schedule_auto_recharge_retry(recharge: WalletAutoRecharge):
    from core.tasks import process_auto_recharge_retry_task

    delay = _retry_delay_seconds()
    process_auto_recharge_retry_task.apply_async(
        args=[str(recharge.id)],
        countdown=max(delay, 1),
    )
    logger.info(
        "Scheduled auto-recharge retry for %s in %ss (recharge_id=%s)",
        recharge.location_id,
        delay,
        recharge.id,
    )


def _credit_wallet_if_needed(wallet: Wallet, credit_amount: Decimal, payment_intent_id: str) -> bool:
    """Credit wallet once per payment_intent_id. Returns True if credit applied."""
    if WalletTransaction.objects.filter(
        wallet=wallet,
        reference_id=payment_intent_id,
        transaction_type="credit",
    ).exists():
        logger.info(
            "Skipping duplicate wallet credit for payment_intent_id=%s",
            payment_intent_id,
        )
        return False
    wallet.add_funds(credit_amount, reference_id=payment_intent_id)
    return True


def _mark_failure(
    recharge: WalletAutoRecharge,
    *,
    code: Optional[str],
    decline_code: Optional[str],
    message: Optional[str],
    category: str,
):
    recharge.last_failure_code = code
    recharge.last_failure_decline_code = decline_code
    recharge.last_failure_message = message
    recharge.failure_category = category
    recharge.last_attempt_at = timezone.now()


def _handle_post_failure(recharge: WalletAutoRecharge, *, is_retry: bool) -> dict[str, Any]:
    """Update status, notify GHL, schedule retry when appropriate."""
    category = recharge.failure_category or WalletAutoRecharge.FAILURE_INTERNAL
    can_retry = (
        category == WalletAutoRecharge.FAILURE_RETRYABLE
        and recharge.attempt_count < recharge.max_attempts
        and not is_retry
    )

    failure_extra = {
        "failure_code": recharge.last_failure_code,
        "decline_code": recharge.last_failure_decline_code,
        "failure_message": recharge.last_failure_message,
        "failure_category": category,
    }

    if can_retry:
        recharge.status = WalletAutoRecharge.STATUS_PENDING_RETRY
        recharge.next_retry_at = timezone.now() + timedelta(seconds=_retry_delay_seconds())
        should_notify = recharge.first_failure_notified_at is None
        if should_notify:
            recharge.first_failure_notified_at = timezone.now()
        recharge.save()
        if should_notify:
            payload = _build_event_payload(
                recharge,
                "auto_recharge.first_failure",
                "warning",
                next_retry_at=recharge.next_retry_at.isoformat() if recharge.next_retry_at else None,
                **failure_extra,
            )
            queue_ghl_auto_recharge_event(payload)
        schedule_auto_recharge_retry(recharge)
        return {
            "success": False,
            "event": "auto_recharge.first_failure",
            "recharge_id": str(recharge.id),
            "status": recharge.status,
            "message": recharge.last_failure_message,
            "code": recharge.last_failure_code,
            "decline_code": recharge.last_failure_decline_code,
            "next_retry_at": recharge.next_retry_at.isoformat() if recharge.next_retry_at else None,
            "http_status": 402,
        }

    # Final / non-retryable
    if category == WalletAutoRecharge.FAILURE_REQUIRES_ACTION and recharge.attempt_count < 2:
        event = "auto_recharge.requires_action"
        recharge.status = WalletAutoRecharge.STATUS_REQUIRES_ACTION
    else:
        event = "auto_recharge.exhausted"
        recharge.status = WalletAutoRecharge.STATUS_EXHAUSTED

    recharge.next_retry_at = None
    should_notify_final = recharge.final_failure_notified_at is None
    if should_notify_final:
        recharge.final_failure_notified_at = timezone.now()
    recharge.save()

    if should_notify_final:
        payload = _build_event_payload(
            recharge,
            event,
            "critical",
            retry_eligible=False,
            **failure_extra,
        )
        queue_ghl_auto_recharge_event(payload)

    return {
        "success": False,
        "event": event,
        "recharge_id": str(recharge.id),
        "status": recharge.status,
        "message": recharge.last_failure_message,
        "code": recharge.last_failure_code,
        "decline_code": recharge.last_failure_decline_code,
        "http_status": 402 if category != WalletAutoRecharge.FAILURE_INTERNAL else 500,
    }


def _finalize_successful_payment(
    recharge: WalletAutoRecharge,
    wallet: Wallet,
    payment_intent,
    *,
    contact_email: str,
    stripe_customer_id: str,
    attempt_number: int,
) -> dict[str, Any]:
    already_succeeded = recharge.status == WalletAutoRecharge.STATUS_SUCCEEDED
    already_notified = recharge.success_notified_at is not None

    _credit_wallet_if_needed(wallet, recharge.credit_amount, payment_intent.id)
    wallet.refresh_from_db()

    recharge.last_payment_intent_id = payment_intent.id
    recharge.status = WalletAutoRecharge.STATUS_SUCCEEDED
    recharge.next_retry_at = None
    recharge.last_failure_code = None
    recharge.last_failure_decline_code = None
    recharge.last_failure_message = None
    recharge.failure_category = None
    recharge.attempt_count = max(recharge.attempt_count, attempt_number)
    if not already_notified:
        recharge.success_notified_at = timezone.now()
    recharge.save()

    if not already_notified:
        payload = _build_event_payload(
            recharge,
            "auto_recharge.succeeded",
            "info",
            payment_intent_id=payment_intent.id,
            charged_amount=float(recharge.charge_amount),
            credited_amount=float(recharge.credit_amount),
            wallet_balance=float(wallet.balance),
        )
        queue_ghl_auto_recharge_event(payload)

    return {
        "success": True,
        "skipped": already_succeeded,
        "event": "auto_recharge.succeeded",
        "message": "Payment completed and wallet credited successfully.",
        "payment_intent_id": payment_intent.id,
        "status": payment_intent.status,
        "charged_amount": float(recharge.charge_amount),
        "credited_amount": float(recharge.credit_amount),
        "wallet_balance": float(wallet.balance),
        "currency": getattr(payment_intent, "currency", "usd"),
        "customer_email": contact_email,
        "stripe_customer_id": stripe_customer_id,
        "location_id": recharge.location_id,
        "recharge_id": str(recharge.id),
        "attempt": attempt_number,
        "http_status": 200,
    }


def _attempt_stripe_charge(recharge: WalletAutoRecharge, wallet: Wallet, attempt_number: int) -> dict[str, Any]:
    _ensure_stripe_api_key()
    account = recharge.account
    contact_email = (account.ghl_contact_email or "").strip()

    # Resume an interrupted success (idempotent)
    if recharge.last_payment_intent_id:
        try:
            existing_pi = stripe.PaymentIntent.retrieve(recharge.last_payment_intent_id)
            if existing_pi.status == "succeeded":
                return _finalize_successful_payment(
                    recharge,
                    wallet,
                    existing_pi,
                    contact_email=contact_email or (recharge.ghl_contact_email or ""),
                    stripe_customer_id=recharge.stripe_customer_id or "",
                    attempt_number=attempt_number,
                )
        except Exception:
            logger.exception(
                "Failed retrieving existing PaymentIntent %s",
                recharge.last_payment_intent_id,
            )

    if not contact_email:
        _mark_failure(
            recharge,
            code="missing_ghl_contact_email",
            decline_code=None,
            message=(
                "GHL Contact Email is not set for this HighLevel account. "
                "Set it in Edit HighLevel Account so Stripe can be looked up."
            ),
            category=WalletAutoRecharge.FAILURE_REQUIRES_ACTION,
        )
        recharge.attempt_count = attempt_number
        recharge.save()
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)

    stripe_customer_id = lookup_latest_stripe_customer_id(contact_email)
    if not stripe_customer_id:
        _mark_failure(
            recharge,
            code="no_stripe_customer",
            decline_code=None,
            message=f"No Stripe customer found for email={contact_email}",
            category=WalletAutoRecharge.FAILURE_REQUIRES_ACTION,
        )
        recharge.attempt_count = attempt_number
        recharge.ghl_contact_email = contact_email
        recharge.save()
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)

    payment_method_id = lookup_stripe_card_payment_method_id(stripe_customer_id)
    if not payment_method_id:
        _mark_failure(
            recharge,
            code="no_payment_method",
            decline_code=None,
            message=(
                f"Stripe customer {stripe_customer_id} has no saved card "
                f"payment method for email={contact_email}"
            ),
            category=WalletAutoRecharge.FAILURE_REQUIRES_ACTION,
        )
        recharge.attempt_count = attempt_number
        recharge.stripe_customer_id = stripe_customer_id
        recharge.ghl_contact_email = contact_email
        recharge.save()
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)

    StripeCustomerData.objects.update_or_create(
        email=contact_email,
        defaults={
            "customer_id": stripe_customer_id,
            "payment_method_id": payment_method_id,
            "location_id": recharge.location_id,
        },
    )

    recharge.stripe_customer_id = stripe_customer_id
    recharge.payment_method_id = payment_method_id
    recharge.ghl_contact_email = contact_email
    recharge.ghl_contact_id = account.ghl_contact_id
    recharge.attempt_count = attempt_number
    recharge.status = WalletAutoRecharge.STATUS_IN_PROGRESS
    recharge.last_attempt_at = timezone.now()
    recharge.save()

    idempotency_key = f"auto-recharge:{recharge.location_id}:{recharge.id}:{attempt_number}"

    try:
        payment_intent = stripe.PaymentIntent.create(
            amount=int(recharge.charge_amount * 100),
            currency="usd",
            customer=stripe_customer_id,
            payment_method=payment_method_id,
            off_session=True,
            confirm=True,
            metadata={
                "location_id": recharge.location_id,
                "ghl_contact_email": contact_email,
                "credit_amount": str(recharge.credit_amount),
                "charge_amount": str(recharge.charge_amount),
                "recharge_id": str(recharge.id),
                "attempt": str(attempt_number),
            },
            idempotency_key=idempotency_key,
        )
        recharge.last_payment_intent_id = payment_intent.id
        recharge.save(update_fields=["last_payment_intent_id", "updated_at"])
    except stripe.CardError as e:
        err = (e.json_body or {}).get("error", {}) if getattr(e, "json_body", None) else {}
        code = err.get("code") or getattr(e, "code", None)
        decline_code = err.get("decline_code")
        message = err.get("message") or str(e)
        category = classify_stripe_failure(code=code, decline_code=decline_code, message=message)
        _mark_failure(
            recharge,
            code=code,
            decline_code=decline_code,
            message=message,
            category=category,
        )
        recharge.save()
        logger.warning(
            "Auto-recharge CardError location=%s attempt=%s code=%s decline=%s",
            recharge.location_id,
            attempt_number,
            code,
            decline_code,
        )
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)
    except stripe.StripeError as e:
        err = (e.json_body or {}).get("error", {}) if getattr(e, "json_body", None) else {}
        code = err.get("code") or getattr(e, "code", None)
        message = err.get("message") or str(e)
        category = WalletAutoRecharge.FAILURE_RETRYABLE
        if code == "authentication_required":
            category = WalletAutoRecharge.FAILURE_REQUIRES_ACTION
        _mark_failure(
            recharge,
            code=code,
            decline_code=err.get("decline_code"),
            message=message,
            category=category,
        )
        recharge.save()
        logger.exception(
            "Auto-recharge StripeError location=%s attempt=%s",
            recharge.location_id,
            attempt_number,
        )
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)

    if payment_intent.status != "succeeded":
        _mark_failure(
            recharge,
            code=payment_intent.status,
            decline_code=None,
            message=f"PaymentIntent status was {payment_intent.status}, expected succeeded",
            category=(
                WalletAutoRecharge.FAILURE_REQUIRES_ACTION
                if payment_intent.status == "requires_action"
                else WalletAutoRecharge.FAILURE_RETRYABLE
            ),
        )
        recharge.save()
        return _handle_post_failure(recharge, is_retry=attempt_number > 1)

    return _finalize_successful_payment(
        recharge,
        wallet,
        payment_intent,
        contact_email=contact_email,
        stripe_customer_id=stripe_customer_id,
        attempt_number=attempt_number,
    )


def _find_active_recharge(location_id: str) -> Optional[WalletAutoRecharge]:
    return (
        WalletAutoRecharge.objects.select_for_update()
        .filter(
            location_id=location_id,
            status__in=WalletAutoRecharge.ACTIVE_STATUSES,
        )
        .first()
    )


def _recent_terminal_blocks_new(location_id: str) -> Optional[WalletAutoRecharge]:
    """Block new cycles shortly after exhausted/requires_action (cooldown)."""
    cutoff = timezone.now() - timedelta(seconds=_cooldown_seconds())
    return (
        WalletAutoRecharge.objects.filter(
            location_id=location_id,
            status__in=(
                WalletAutoRecharge.STATUS_EXHAUSTED,
                WalletAutoRecharge.STATUS_REQUIRES_ACTION,
            ),
            updated_at__gte=cutoff,
        )
        .order_by("-updated_at")
        .first()
    )


def handle_create_deduction_request(
    *,
    location_id: str,
    recharge_text: str,
) -> dict[str, Any]:
    """
    Main entry for GHL create-deduction webhook.
    Returns a dict including http_status for the view to use.
    """
    charge_amount, credit_amount, parse_error = parse_recharge_charge_and_credit(recharge_text)
    if parse_error:
        return {"success": False, "error": parse_error, "http_status": 400}

    try:
        account = GHLAuthCredentials.objects.get(location_id=location_id)
    except GHLAuthCredentials.DoesNotExist:
        return {
            "success": False,
            "error": f"GHL account not found for location_id={location_id}",
            "http_status": 404,
        }

    recharge_id = None
    with transaction.atomic():
        wallet, _ = Wallet.objects.select_for_update().get_or_create(account=account)

        if wallet.balance > SKIP_BALANCE_THRESHOLD:
            return {
                "success": True,
                "skipped": True,
                "message": "Wallet already has sufficient balance; no Stripe charge performed.",
                "wallet_balance": float(wallet.balance),
                "minimum_balance_to_skip": float(SKIP_BALANCE_THRESHOLD),
                "location_id": location_id,
                "reason": "sufficient_balance",
                "http_status": 200,
            }

        active = _find_active_recharge(location_id)
        if active:
            return {
                "success": True,
                "skipped": True,
                "message": "An auto-recharge is already in progress or scheduled for retry.",
                "wallet_balance": float(wallet.balance),
                "location_id": location_id,
                "recharge_id": str(active.id),
                "status": active.status,
                "reason": "recharge_in_progress",
                "next_retry_at": active.next_retry_at.isoformat() if active.next_retry_at else None,
                "http_status": 200,
            }

        blocked = _recent_terminal_blocks_new(location_id)
        if blocked:
            return {
                "success": True,
                "skipped": True,
                "message": "Auto-recharge recently exhausted; cooldown active.",
                "wallet_balance": float(wallet.balance),
                "location_id": location_id,
                "recharge_id": str(blocked.id),
                "status": blocked.status,
                "reason": "cooldown",
                "http_status": 200,
            }

        try:
            recharge = WalletAutoRecharge.objects.create(
                account=account,
                location_id=location_id,
                status=WalletAutoRecharge.STATUS_IN_PROGRESS,
                attempt_count=0,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                charge_amount=charge_amount,
                credit_amount=credit_amount,
                recharge_text=str(recharge_text or "")[:255],
                ghl_contact_email=account.ghl_contact_email,
                ghl_contact_id=account.ghl_contact_id,
            )
            recharge_id = recharge.id
        except IntegrityError:
            active = _find_active_recharge(location_id)
            return {
                "success": True,
                "skipped": True,
                "message": "An auto-recharge is already in progress or scheduled for retry.",
                "location_id": location_id,
                "recharge_id": str(active.id) if active else None,
                "reason": "recharge_in_progress",
                "http_status": 200,
            }

    # Stripe network call outside DB locks
    wallet = Wallet.objects.get(account=account)
    if wallet.balance > SKIP_BALANCE_THRESHOLD:
        WalletAutoRecharge.objects.filter(pk=recharge_id).update(
            status=WalletAutoRecharge.STATUS_SKIPPED,
            skip_reason="sufficient_balance",
            updated_at=timezone.now(),
        )
        return {
            "success": True,
            "skipped": True,
            "message": "Wallet already has sufficient balance; no Stripe charge performed.",
            "wallet_balance": float(wallet.balance),
            "location_id": location_id,
            "recharge_id": str(recharge_id),
            "reason": "sufficient_balance",
            "http_status": 200,
        }

    recharge = WalletAutoRecharge.objects.select_related("account").get(pk=recharge_id)
    return _attempt_stripe_charge(recharge, wallet, attempt_number=1)


STALE_IN_PROGRESS_SECONDS = 15 * 60


def process_auto_recharge_retry(recharge_id: str) -> dict[str, Any]:
    """Run attempt 2 (or overdue pending_retry). Idempotent if already terminal."""
    next_attempt = None
    stale_cutoff = timezone.now() - timedelta(seconds=STALE_IN_PROGRESS_SECONDS)

    with transaction.atomic():
        try:
            recharge = WalletAutoRecharge.objects.select_for_update().select_related(
                "account"
            ).get(pk=recharge_id)
        except WalletAutoRecharge.DoesNotExist:
            return {"success": False, "error": "recharge_not_found", "http_status": 404}

        if recharge.status == WalletAutoRecharge.STATUS_SUCCEEDED:
            return {
                "success": True,
                "skipped": True,
                "reason": "already_succeeded",
                "recharge_id": str(recharge.id),
                "http_status": 200,
            }

        if recharge.status in (
            WalletAutoRecharge.STATUS_EXHAUSTED,
            WalletAutoRecharge.STATUS_REQUIRES_ACTION,
            WalletAutoRecharge.STATUS_SKIPPED,
            WalletAutoRecharge.STATUS_CANCELLED,
        ):
            return {
                "success": True,
                "skipped": True,
                "reason": f"already_{recharge.status}",
                "recharge_id": str(recharge.id),
                "http_status": 200,
            }

        is_pending = recharge.status == WalletAutoRecharge.STATUS_PENDING_RETRY
        is_stale_in_progress = (
            recharge.status == WalletAutoRecharge.STATUS_IN_PROGRESS
            and recharge.updated_at <= stale_cutoff
        )
        if not is_pending and not is_stale_in_progress:
            return {
                "success": False,
                "skipped": True,
                "reason": f"unexpected_status_{recharge.status}",
                "recharge_id": str(recharge.id),
                "http_status": 200,
            }

        wallet, _ = Wallet.objects.select_for_update().get_or_create(account=recharge.account)

        if wallet.balance > SKIP_BALANCE_THRESHOLD:
            recharge.status = WalletAutoRecharge.STATUS_CANCELLED
            recharge.skip_reason = "balance_recovered"
            recharge.next_retry_at = None
            recharge.save(
                update_fields=["status", "skip_reason", "next_retry_at", "updated_at"]
            )
            logger.info(
                "Cancelled auto-recharge retry for %s — balance recovered (%.2f)",
                recharge.location_id,
                wallet.balance,
            )
            return {
                "success": True,
                "skipped": True,
                "reason": "balance_recovered",
                "wallet_balance": float(wallet.balance),
                "recharge_id": str(recharge.id),
                "http_status": 200,
            }

        next_attempt = (
            recharge.attempt_count + 1
            if is_pending
            else max(recharge.attempt_count, 1)
        )
        if next_attempt > recharge.max_attempts:
            recharge.status = WalletAutoRecharge.STATUS_EXHAUSTED
            recharge.next_retry_at = None
            recharge.save(update_fields=["status", "next_retry_at", "updated_at"])
            return {
                "success": False,
                "reason": "max_attempts_exceeded",
                "recharge_id": str(recharge.id),
                "http_status": 200,
            }

        # Claim so concurrent sweeper/task won't double-run
        recharge.status = WalletAutoRecharge.STATUS_IN_PROGRESS
        recharge.next_retry_at = None
        recharge.save(update_fields=["status", "next_retry_at", "updated_at"])
        account_id = recharge.account_id
        pk = recharge.id

    recharge = WalletAutoRecharge.objects.select_related("account").get(pk=pk)
    wallet = Wallet.objects.get(account_id=account_id)
    return _attempt_stripe_charge(recharge, wallet, attempt_number=next_attempt)


def sweep_due_auto_recharges() -> dict[str, Any]:
    """Safety net: enqueue overdue pending_retry and stale in_progress rows."""
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=STALE_IN_PROGRESS_SECONDS)

    due_ids = list(
        WalletAutoRecharge.objects.filter(
            status=WalletAutoRecharge.STATUS_PENDING_RETRY,
            next_retry_at__lte=now,
        ).values_list("id", flat=True)[:100]
    )
    stuck_ids = list(
        WalletAutoRecharge.objects.filter(
            status=WalletAutoRecharge.STATUS_IN_PROGRESS,
            updated_at__lte=stale_cutoff,
        ).values_list("id", flat=True)[:100]
    )
    all_ids = list(dict.fromkeys([*due_ids, *stuck_ids]))

    from core.tasks import process_auto_recharge_retry_task

    for rid in all_ids:
        process_auto_recharge_retry_task.delay(str(rid))

    logger.info(
        "Swept auto-recharges: due=%s stuck=%s total_enqueued=%s",
        len(due_ids),
        len(stuck_ids),
        len(all_ids),
    )
    return {"enqueued": len(all_ids), "due": len(due_ids), "stuck": len(stuck_ids)}
