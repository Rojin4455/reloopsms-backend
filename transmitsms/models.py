import uuid

from django.db import models

# Create your models here.

class TransmitSMSAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_name = models.CharField(max_length=150)
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)  # MSISDN format
    api_key = models.CharField(max_length=255)
    api_secret = models.CharField(max_length=255)
    account_id = models.CharField(max_length=100, unique=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default='AUD')
    timezone = models.CharField(max_length=50, default='Australia/Brisbane')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"TransmitSMS Account {self.account_name} ({self.account_id})"