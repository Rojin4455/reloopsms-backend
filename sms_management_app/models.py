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

    def __str__(self):
        return f"ID {self.id} - {self.ghl_account.user_id} → {self.transmit_account.account_name}"
