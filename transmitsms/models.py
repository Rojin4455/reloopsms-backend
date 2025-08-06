import uuid

from django.db import models

# Create your models here.

class TransmitSMSAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    api_key = models.CharField(max_length=255)
    api_secret = models.CharField(max_length=255)
    account_id = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"TransmitSMS Account ({self.account_id})"
