from django.contrib import admin
from .models import TransmitSMSMMSWebhook


@admin.register(TransmitSMSMMSWebhook)
class TransmitSMSMMSWebhookAdmin(admin.ModelAdmin):
    list_display = ('webhook_id', 'webhook_name', 'transmit_account', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('webhook_id', 'webhook_name')
