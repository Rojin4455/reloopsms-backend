import uuid

from django.db import models

# Create your models here.

class TransmitSMSAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_name = models.CharField(max_length=150)
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)  # MSISDN format
    password = models.CharField(max_length=100, null=True, blank=True)
    api_key = models.CharField(max_length=255)
    api_secret = models.CharField(max_length=255)
    account_id = models.CharField(max_length=100, unique=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    # True = legacy "Client Pays" in TransmitSMS (wholesale debited from this subaccount).
    # False = "I Pay" / agency wholesale wallet is debited instead.
    client_pays = models.BooleanField(default=False)
    balance_synced_at = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=10, default='AUD')
    timezone = models.CharField(max_length=50, default='Australia/Brisbane')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"TransmitSMS Account {self.account_name} ({self.account_id})"


class TransmitAgencyBalance(models.Model):
    """
    Cached agency wholesale balance from TransmitSMS (singleton row, pk=1).
    Updated manually via the admin refresh button — no Celery sync.
    """
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default="AUD")
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Transmit agency balance snapshot"
        verbose_name_plural = "Transmit agency balance snapshots"

    @classmethod
    def get_snapshot(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"Agency balance: {self.balance} {self.currency}"


class TransmitSMSMMSWebhook(models.Model):
    """
    Maps TransmitSMS MMS inbound webhook_id to a TransmitSMS account.
    When MMS_INBOUND event arrives, we look up by webhook_id to identify
    the correct transmit account, then resolve the GHL account via mapping.
    """
    transmit_account = models.OneToOneField(
        TransmitSMSAccount,
        on_delete=models.CASCADE,
        related_name="mms_webhook_config",
        primary_key=True,
    )
    webhook_id = models.CharField(max_length=255, unique=True)
    webhook_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "TransmitSMS MMS Webhook"
        verbose_name_plural = "TransmitSMS MMS Webhooks"

    def __str__(self):
        return f"MMS Webhook {self.webhook_name or self.webhook_id} → {self.transmit_account.account_name}"

