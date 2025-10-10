from django.db import models, transaction
import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import now
from decimal import Decimal

# Create your models here.


class AgencyToken(models.Model):
    access_token = models.TextField()
    token_type = models.CharField(max_length=50, default='Bearer')
    expires_in = models.PositiveIntegerField(default=86399)
    refresh_token = models.TextField()
    scope = models.CharField(max_length=255, blank=True, null=True)
    refresh_token_id = models.CharField(max_length=128, blank=True, null=True)
    user_type = models.CharField(max_length=50, default='Company')
    company_id = models.CharField(max_length=128, db_index=True)
    is_bulk_installation = models.BooleanField(default=False)
    user_id = models.CharField(max_length=128, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class GHLAuthCredentials(models.Model):
    user_id = models.CharField(max_length=255)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_in = models.IntegerField()
    scope = models.CharField(max_length=500, null=True, blank=True)
    user_type = models.CharField(max_length=50, null=True, blank=True)
    company_id = models.CharField(max_length=255, null=True, blank=True)
    location_name = models.CharField(max_length=255, null=True, blank=True)
    timezone = models.CharField(max_length=100, null=True, blank=True, default="")
    location_id = models.CharField(max_length=255, null=True, blank=True)

    business_email = models.EmailField(null=True, blank=True, help_text="Business email for TransmitSMS account")
    business_phone = models.CharField(max_length=20, null=True, blank=True, help_text="Business phone number in E.164 format")
    contact_name = models.CharField(max_length=255, null=True, blank=True, help_text="Primary contact name")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} - {self.company_id}"

class Wallet(models.Model):
    """Wallet for each GHL account"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(GHLAuthCredentials, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    inbound_segment_charge = models.DecimalField(max_digits=6, decimal_places=2, default=0.00)  # per segment
    outbound_segment_charge = models.DecimalField(max_digits=6, decimal_places=3, default=0.074)

    ghl_object_id = models.CharField(max_length=255, blank=True, null=True)
    
    cred_purchased = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.00"))
    cred_spent = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.00"))
    cred_remaining = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.00"))

    seg_purchased = models.BigIntegerField(default=0)
    seg_remaining = models.BigIntegerField(default=0)
    seg_used = models.BigIntegerField(default=0)

    business_name = models.CharField(max_length=255, blank=True, null=True)
    contact = models.CharField(max_length=255, blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet for {self.account.user_id} - Balance: {self.balance}"

    def charge_message(self, direction: str, message: str, reference_id=None):
        """Charge wallet for inbound or outbound message"""
        segments = (len(message) // 160) + (1 if len(message) % 160 else 0)

        if direction == "inbound":
            cost = segments * self.inbound_segment_charge
        elif direction == "outbound":
            cost = segments * self.outbound_segment_charge
        else:
            raise ValidationError("Invalid message direction")

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            if wallet.balance < cost:
                raise ValidationError("Insufficient balance to send message")

            # Deduct balance
            wallet.balance -= cost

            if direction == "outbound":
                # Update credits + segments
                wallet.cred_spent += cost
                wallet.cred_remaining -= cost

                wallet.seg_used += segments
                wallet.seg_remaining -= segments

            wallet.save(update_fields=[
                "balance", "cred_spent", "cred_remaining", "seg_used", "seg_remaining"
            ])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="debit",
                amount=cost,
                balance_after=wallet.balance,
                description=f"Charged for {direction} SMS ({segments} segments)",
                reference_id=reference_id
            )

        return cost, segments
    
    def refund(self, amount, *, reference_id=None, description="Refund"):
        """Refund credits back to wallet"""

        amount = Decimal(str(amount))
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            wallet.balance += amount
            wallet.cred_remaining += amount

            # derive segments from refunded amount
            segments = int(amount / wallet.outbound_segment_charge)
            wallet.seg_remaining += segments
            wallet.seg_used -= segments

            wallet.save(update_fields=["balance", "cred_remaining", "seg_remaining","seg_used"])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="credit",
                amount=amount,
                balance_after=wallet.balance,
                description=description,
                reference_id=reference_id
            )


    def add_funds(self, amount: float, reference_id=None, gift=None):
        """Add funds from webhook/payment"""

        
        from django.utils import timezone

        amount = Decimal(str(amount))
        print(f"\n===== Add Funds =====")
        print(f"Initial amount: {amount}")
        print(f"Wallet before add: balance={self.balance}, cred_purchased={self.cred_purchased}, "
            f"cred_remaining={self.cred_remaining}, seg_purchased={self.seg_purchased}, "
            f"seg_remaining={self.seg_remaining}, outbound_segment_charge={self.outbound_segment_charge}")

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            wallet.balance += amount
            wallet.cred_purchased += amount
            wallet.cred_remaining += amount

            # derive segments purchased from credits
            if wallet.outbound_segment_charge:
                segments = int(amount / wallet.outbound_segment_charge)
            else:
                segments=0
            wallet.seg_purchased += segments
            wallet.seg_remaining += segments
            

            print(f"Derived segments from added credits: {segments}")
            print(f"Wallet after computation: balance={wallet.balance}, cred_purchased={wallet.cred_purchased}, "
                f"cred_remaining={wallet.cred_remaining}, seg_purchased={wallet.seg_purchased}, "
                f"seg_remaining={wallet.seg_remaining}")

            wallet.save(update_fields=[
                "balance", "cred_purchased", "cred_remaining",
                "seg_purchased", "seg_remaining"
            ])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="credit",
                amount=amount,
                balance_after=wallet.balance,
                description="Gifted Funds" if gift else "Funds added",
                reference_id=reference_id
            )

            print(f"WalletTransaction created at {timezone.now()} for amount={amount}, balance_after={wallet.balance}")

        print(f"Final Wallet state: balance={wallet.balance}, cred_remaining={wallet.cred_remaining}, "
            f"seg_remaining={wallet.seg_remaining}\n")


        # ✅ After funds are added, retry queued SMS (both outbound + inbound)
        from sms_management_app.tasks import process_sms_message

        queued_messages = self.account.smsmessage_set.filter(status="queued").order_by("created_at")
        for sms in queued_messages:
            if sms.direction == "outbound":
                # keep your existing logic — outbound is handled here directly
                try:
                    cost, segments = self.charge_message("outbound", sms.message_content, reference_id=sms.id)
                    from sms_management_app.services import GHLIntegrationService
                    service = GHLIntegrationService()
                    result = service.send_outbound_sms(sms, cost, segments)
                    if result["success"]:
                        sms.status = "sent"
                        sms.sent_at = timezone.now()
                        sms.transmit_message_id = result.get("transmit_message_id")
                    else:
                        # Refund if failed
                        self.refund(cost, reference_id=sms.id, description="Refund for failed queued SMS")
                        sms.status = "failed"
                        sms.error_message = result["error"]
                except ValidationError:
                    sms.status = "queued"  # still insufficient funds
                sms.save()

            elif sms.direction == "inbound":
                # ✅ For inbound, just enqueue the Celery task to handle rate limits
                process_sms_message.delay(str(sms.id))

        return self.balance
    


    def deduct_funds(self, amount: float, reference_id=None, description="Funds deducted"):
        """Deduct funds from the wallet (used for taking or adjusting funds)."""
        from django.utils import timezone
        amount = Decimal(str(amount))

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            if wallet.balance < amount:
                raise ValidationError("Insufficient balance to deduct funds.")

            wallet.balance -= amount
            wallet.cred_remaining -= amount

            # derive segments removed based on segment charge
            if wallet.outbound_segment_charge:
                segments = int(amount / wallet.outbound_segment_charge)
            else:
                segments=0
            wallet.seg_remaining = max(wallet.seg_remaining - segments, 0)

            wallet.save(update_fields=["balance", "cred_remaining", "seg_remaining"])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="debit",
                amount=amount,
                balance_after=wallet.balance,
                description=description,
                reference_id=reference_id
            )

            print(f"💸 Funds deducted: {amount}, balance now {wallet.balance} at {timezone.now()}")

        return wallet.balance
    
class WalletTransaction(models.Model):
    TRANSACTION_TYPES = (
        ("credit", "Credit"),   # add funds
        ("debit", "Debit"),     # charge for SMS
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey("Wallet", on_delete=models.CASCADE, related_name="transactions")

    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=10, decimal_places=3)
    balance_after = models.DecimalField(max_digits=10, decimal_places=3)  # snapshot of balance

    description = models.TextField(null=True, blank=True)  # e.g. "Charged for outbound SMS", "Payment via Stripe"
    reference_id = models.CharField(max_length=255, null=True, blank=True)  # payment gateway txn ID or sms ID

    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.wallet.account.user_id} | {self.transaction_type} {self.amount} | Balance: {self.balance_after}"




class TransmitNumber(models.Model):
    STATUS_CHOICES = [
        ("available", "Available"),
        ("registered", "Registered"),
        ("owned", "Owned"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ghl_account = models.ForeignKey(
        GHLAuthCredentials,
        on_delete=models.CASCADE,
        related_name="transmit_numbers",
        null=True, blank=True,
        help_text="The GHL account this number is linked to"
    )
    number = models.CharField(max_length=20, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="available")
    is_active = models.BooleanField(default=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.number} ({self.status})"
    


class StripeCustomer(models.Model):
    email = models.EmailField(unique=True)
    customer_id = models.CharField(max_length=255)
    payment_method_id = models.CharField(max_length=255, blank=True, null=True)
    location_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.customer_id})"
    

class StripeCustomerData(models.Model):
    email = models.EmailField(unique=True)
    customer_id = models.CharField(max_length=255)
    payment_method_id = models.CharField(max_length=255, blank=True, null=True)
    location_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.customer_id})"