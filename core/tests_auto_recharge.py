"""
Safe auto-recharge tests — SQLite + mocked Stripe/GHL only.
Never connects to production RDS when run with test_settings_isolated.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.auto_recharge import (
    classify_stripe_failure,
    extract_create_deduction_params,
    handle_create_deduction_request,
    parse_recharge_charge_and_credit,
    process_auto_recharge_retry,
)
from core.models import GHLAuthCredentials, Wallet, WalletAutoRecharge, WalletTransaction


def _make_account(**kwargs):
    defaults = {
        "user_id": "user-1",
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_in": 3600,
        "location_id": "loc_test_1",
        "location_name": "Test Location",
        "ghl_contact_email": "billing@example.com",
        "ghl_contact_id": "contact_1",
    }
    defaults.update(kwargs)
    from django.db.models.signals import post_save
    from core import signals as core_signals

    post_save.disconnect(core_signals.sync_wallet_with_ghl, sender=GHLAuthCredentials)
    try:
        return GHLAuthCredentials.objects.create(**defaults)
    finally:
        post_save.connect(core_signals.sync_wallet_with_ghl, sender=GHLAuthCredentials)


class ParseAndClassifyTests(TestCase):
    def test_parse_recharge_text(self):
        charge, credit, err = parse_recharge_charge_and_credit("$30.00 Credit + $1 Card Fee")
        self.assertIsNone(err)
        self.assertEqual(charge, Decimal("31.00"))
        self.assertEqual(credit, Decimal("30.00"))

    def test_extract_params_custom_data(self):
        params = extract_create_deduction_params(
            {
                "customData": {
                    "SMS Recharge LocationID": "abc123",
                    "SMS Credit Recharge": "$30.00 Credit + $1 Card Fee",
                }
            }
        )
        self.assertEqual(params["location_id"], "abc123")
        self.assertEqual(params["recharge_text"], "$30.00 Credit + $1 Card Fee")

    def test_extract_params_blank_top_level_uses_custom_data(self):
        params = extract_create_deduction_params(
            {
                "SMS Recharge LocationID": "uaTMEOOGUajrDDaLxJWO",
                "SMS Credit Recharge": "",
                "customData": {
                    "SMS Recharge LocationID": "uaTMEOOGUajrDDaLxJWO",
                    "SMS Credit Recharge": "$30.00 Credit + $1 Card Fee",
                },
            }
        )
        self.assertEqual(params["location_id"], "uaTMEOOGUajrDDaLxJWO")
        self.assertEqual(params["recharge_text"], "$30.00 Credit + $1 Card Fee")

    def test_classify_failures(self):
        self.assertEqual(
            classify_stripe_failure(code="expired_card"),
            WalletAutoRecharge.FAILURE_REQUIRES_ACTION,
        )
        self.assertEqual(
            classify_stripe_failure(code="card_declined", decline_code="insufficient_funds"),
            WalletAutoRecharge.FAILURE_RETRYABLE,
        )
        self.assertEqual(
            classify_stripe_failure(code="authentication_required"),
            WalletAutoRecharge.FAILURE_REQUIRES_ACTION,
        )


@override_settings(
    GHL_AUTO_RECHARGE_WEBHOOK_URL="https://example.test/webhook",
    AUTO_RECHARGE_RETRY_DELAY_SECONDS=7200,
    AUTO_RECHARGE_COOLDOWN_SECONDS=86400,
)
class AutoRechargeFlowTests(TestCase):
    def setUp(self):
        self.account = _make_account()
        self.wallet = Wallet.objects.create(account=self.account, balance=Decimal("0.50"))

    def _recharge_text(self):
        return "$30.00 Credit + $1 Card Fee"

    @patch("core.auto_recharge.schedule_auto_recharge_retry")
    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_success_credits_wallet_and_notifies(
        self, mock_create, _cus, _pm, mock_notify, mock_schedule
    ):
        mock_create.return_value = MagicMock(
            id="pi_success_1",
            status="succeeded",
            currency="usd",
        )

        result = handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["http_status"], 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("30.50"))
        self.assertTrue(
            WalletTransaction.objects.filter(reference_id="pi_success_1").exists()
        )
        mock_notify.assert_called()
        event = mock_notify.call_args[0][0]["event"]
        self.assertEqual(event, "auto_recharge.succeeded")
        mock_schedule.assert_not_called()
        # Idempotency key present
        self.assertIn("idempotency_key", mock_create.call_args.kwargs)

    def test_skip_when_balance_high(self):
        self.wallet.balance = Decimal("10.00")
        self.wallet.save(update_fields=["balance"])

        result = handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "sufficient_balance")
        self.assertFalse(WalletAutoRecharge.objects.exists())

    @patch("core.auto_recharge.schedule_auto_recharge_retry")
    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_first_failure_schedules_retry(
        self, mock_create, _cus, _pm, mock_notify, mock_schedule
    ):
        err = MagicMock()
        err.json_body = {
            "error": {
                "code": "card_declined",
                "decline_code": "insufficient_funds",
                "message": "Your card has insufficient funds.",
            }
        }
        # stripe.error.CardError needs to be raised as the real type
        import stripe

        card_error = stripe.CardError(
            message="Your card has insufficient funds.",
            param=None,
            code="card_declined",
            json_body=err.json_body,
            http_status=402,
        )
        mock_create.side_effect = card_error

        result = handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["event"], "auto_recharge.first_failure")
        self.assertEqual(result["http_status"], 402)

        row = WalletAutoRecharge.objects.get()
        self.assertEqual(row.status, WalletAutoRecharge.STATUS_PENDING_RETRY)
        self.assertEqual(row.attempt_count, 1)
        self.assertIsNotNone(row.next_retry_at)
        mock_schedule.assert_called_once()
        mock_notify.assert_called()
        self.assertEqual(mock_notify.call_args[0][0]["event"], "auto_recharge.first_failure")
        # Wallet unchanged
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0.50"))

    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_expired_card_requires_action_no_retry(
        self, mock_create, _cus, _pm, mock_notify
    ):
        import stripe

        mock_create.side_effect = stripe.CardError(
            message="Your card has expired.",
            param=None,
            code="expired_card",
            json_body={"error": {"code": "expired_card", "message": "Your card has expired."}},
            http_status=402,
        )

        with patch("core.auto_recharge.schedule_auto_recharge_retry") as mock_schedule:
            result = handle_create_deduction_request(
                location_id=self.account.location_id,
                recharge_text=self._recharge_text(),
            )

        self.assertEqual(result["event"], "auto_recharge.requires_action")
        row = WalletAutoRecharge.objects.get()
        self.assertEqual(row.status, WalletAutoRecharge.STATUS_REQUIRES_ACTION)
        mock_schedule.assert_not_called()
        self.assertEqual(mock_notify.call_args[0][0]["event"], "auto_recharge.requires_action")

    @patch("core.auto_recharge.schedule_auto_recharge_retry")
    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    def test_duplicate_webhook_skipped_while_pending(self, mock_notify, mock_schedule):
        WalletAutoRecharge.objects.create(
            account=self.account,
            location_id=self.account.location_id,
            status=WalletAutoRecharge.STATUS_PENDING_RETRY,
            attempt_count=1,
            max_attempts=2,
            charge_amount=Decimal("31.00"),
            credit_amount=Decimal("30.00"),
            next_retry_at=timezone.now() + timedelta(hours=2),
        )

        result = handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "recharge_in_progress")
        self.assertEqual(WalletAutoRecharge.objects.count(), 1)

    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_retry_skips_when_balance_recovered(self, mock_create, _cus, _pm, mock_notify):
        row = WalletAutoRecharge.objects.create(
            account=self.account,
            location_id=self.account.location_id,
            status=WalletAutoRecharge.STATUS_PENDING_RETRY,
            attempt_count=1,
            max_attempts=2,
            charge_amount=Decimal("31.00"),
            credit_amount=Decimal("30.00"),
            next_retry_at=timezone.now(),
        )
        self.wallet.balance = Decimal("25.00")
        self.wallet.save(update_fields=["balance"])

        result = process_auto_recharge_retry(str(row.id))
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "balance_recovered")
        row.refresh_from_db()
        self.assertEqual(row.status, WalletAutoRecharge.STATUS_CANCELLED)
        mock_create.assert_not_called()

    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_retry_second_failure_exhausted(self, mock_create, _cus, _pm, mock_notify):
        import stripe

        row = WalletAutoRecharge.objects.create(
            account=self.account,
            location_id=self.account.location_id,
            status=WalletAutoRecharge.STATUS_PENDING_RETRY,
            attempt_count=1,
            max_attempts=2,
            charge_amount=Decimal("31.00"),
            credit_amount=Decimal("30.00"),
            next_retry_at=timezone.now(),
            first_failure_notified_at=timezone.now(),
        )
        mock_create.side_effect = stripe.CardError(
            message="Your card was declined.",
            param=None,
            code="card_declined",
            json_body={
                "error": {
                    "code": "card_declined",
                    "decline_code": "insufficient_funds",
                    "message": "Your card was declined.",
                }
            },
            http_status=402,
        )

        result = process_auto_recharge_retry(str(row.id))
        self.assertFalse(result["success"])
        self.assertEqual(result["event"], "auto_recharge.exhausted")
        row.refresh_from_db()
        self.assertEqual(row.status, WalletAutoRecharge.STATUS_EXHAUSTED)
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(mock_notify.call_args[0][0]["event"], "auto_recharge.exhausted")

    @patch("core.auto_recharge.schedule_auto_recharge_retry")
    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    def test_cooldown_blocks_new_cycle(self, mock_notify, mock_schedule):
        WalletAutoRecharge.objects.create(
            account=self.account,
            location_id=self.account.location_id,
            status=WalletAutoRecharge.STATUS_EXHAUSTED,
            attempt_count=2,
            max_attempts=2,
            charge_amount=Decimal("31.00"),
            credit_amount=Decimal("30.00"),
        )

        result = handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "cooldown")

    @patch("core.auto_recharge.queue_ghl_auto_recharge_event")
    @patch("core.auto_recharge.lookup_stripe_card_payment_method_id", return_value="pm_test")
    @patch("core.auto_recharge.lookup_latest_stripe_customer_id", return_value="cus_test")
    @patch("core.auto_recharge.stripe.PaymentIntent.create")
    def test_no_duplicate_credit_same_payment_intent(
        self, mock_create, _cus, _pm, mock_notify
    ):
        mock_create.return_value = MagicMock(
            id="pi_dup",
            status="succeeded",
            currency="usd",
        )
        handle_create_deduction_request(
            location_id=self.account.location_id,
            recharge_text=self._recharge_text(),
        )
        self.wallet.refresh_from_db()
        balance_after = self.wallet.balance

        # Simulate resume finalize with same PI
        row = WalletAutoRecharge.objects.get()
        from core.auto_recharge import _finalize_successful_payment

        _finalize_successful_payment(
            row,
            self.wallet,
            MagicMock(id="pi_dup", status="succeeded", currency="usd"),
            contact_email="billing@example.com",
            stripe_customer_id="cus_test",
            attempt_number=1,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, balance_after)
        self.assertEqual(
            WalletTransaction.objects.filter(reference_id="pi_dup").count(),
            1,
        )
