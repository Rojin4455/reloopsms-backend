from django.db import models, transaction
import uuid
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import now

# Create your models here.

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

    inbound_segment_charge = models.DecimalField(max_digits=6, decimal_places=2, default=0.01)  # per segment
    outbound_segment_charge = models.DecimalField(max_digits=6, decimal_places=2, default=0.02)  # per segment

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
            # Lock the wallet row for update
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            if wallet.balance < cost:
                raise ValidationError("Insufficient balance to send message")

            wallet.balance -= cost
            wallet.save(update_fields=["balance"])

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
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)
            wallet.balance += amount
            wallet.save(update_fields=["balance"])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="credit",
                amount=amount,
                balance_after=wallet.balance,
                description=description,
                reference_id=reference_id
            )

    def add_funds(self, amount: float, reference_id=None):
        """Add funds from webhook/payment"""
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)
            wallet.balance += amount
            wallet.save(update_fields=["balance"])

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type="credit",
                amount=amount,
                balance_after=wallet.balance,
                description="Funds added",
                reference_id=reference_id
            )

        from .services import GHLIntegrationService  # import inside to avoid circular import
        service = GHLIntegrationService()

        queued_messages = self.account.smsmessage_set.filter(status="queued").order_by("created_at")
        for sms in queued_messages:
            if sms.direction == 'outbound':
                try:
                    cost, segments = self.charge_message("outbound", sms.message_content)
                    result = service.send_outbound_sms(sms, cost, segments)
                    if result["success"]:
                        sms.status = "sent"
                        sms.sent_at = timezone.now()
                        sms.transmit_message_id = result.get("transmit_message_id")
                    else:
                         # refund since SMS failed after charging
                        self.refund(
                            cost,
                            reference_id=sms.id,
                            description="Refund for failed queued SMS"
                        )
                        sms.status = "failed"
                        sms.error_message = result["error"]

                    sms.save()
                except ValidationError:
                    # stop if balance is still insufficient
                    break

        return self.balance
    
class WalletTransaction(models.Model):
    TRANSACTION_TYPES = (
        ("credit", "Credit"),   # add funds
        ("debit", "Debit"),     # charge for SMS
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey("Wallet", on_delete=models.CASCADE, related_name="transactions")

    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot of balance

    description = models.TextField(null=True, blank=True)  # e.g. "Charged for outbound SMS", "Payment via Stripe"
    reference_id = models.CharField(max_length=255, null=True, blank=True)  # payment gateway txn ID or sms ID

    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.wallet.account.user_id} | {self.transaction_type} {self.amount} | Balance: {self.balance_after}"