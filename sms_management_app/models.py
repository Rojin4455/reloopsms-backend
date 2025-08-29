from django.db import models
from core.models import GHLAuthCredentials
from transmitsms.models import TransmitSMSAccount
import uuid

# models.py
class GHLTransmitSMSMapping(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ghl_account = models.OneToOneField(
        GHLAuthCredentials, on_delete=models.CASCADE, related_name="transmit_sms_mapping"
    )
    transmit_account = models.OneToOneField(
        TransmitSMSAccount, on_delete=models.CASCADE, related_name="ghl_mapping"
    )
    mapped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['ghl_account', 'transmit_account']

    def __str__(self):
        return f"{self.ghl_account.location_name} → {self.transmit_account.account_name}"
    


class SMSMessage(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
        ('expired', 'Expired'),
        ("queued", "Queued (no balance)"),
    ]
    
    DIRECTION_CHOICES = [
        ('outbound', 'Outbound'),
        ('inbound', 'Inbound'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ghl_account = models.ForeignKey(GHLAuthCredentials, on_delete=models.CASCADE)
    transmit_account = models.ForeignKey(TransmitSMSAccount, on_delete=models.CASCADE)
    
    # Message details
    message_content = models.TextField()
    to_number = models.CharField(max_length=20)
    from_number = models.CharField(max_length=20)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    
    # GHL specific fields
    ghl_message_id = models.CharField(max_length=255, null=True, blank=True)
    ghl_conversation_id = models.CharField(max_length=255, null=True, blank=True)
    ghl_contact_id = models.CharField(max_length=255, null=True, blank=True)
    
    # TransmitSMS specific fields
    transmit_message_id = models.CharField(max_length=255, null=True, blank=True)
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    delivery_status = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    cost = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    segments = models.PositiveIntegerField(default=1)
    
    # Timestamps
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SMS {self.direction} - {self.to_number} [{self.status}]"

class WebhookLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook_type = models.CharField(max_length=50)  # 'ghl_inbound', 'transmit_dlr', 'transmit_reply'
    raw_data = models.JSONField()
    processed = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.webhook_type} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"


