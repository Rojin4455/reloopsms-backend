from django.contrib import admin
from .models import TransmitSMSAccount,GHLTransmitSMSMapping,SMSMessage
from core.models import GHLAuthCredentials

admin.site.register(TransmitSMSAccount)
admin.site.register(GHLTransmitSMSMapping)
admin.site.register(GHLAuthCredentials)
admin.site.register(SMSMessage)

# Register your models here.
