from rest_framework import serializers

from .models import TransmitSMSAccount, TransmitSMSMMSWebhook


class TransmitSMSAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransmitSMSAccount
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class TransmitSMSMMSWebhookSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransmitSMSMMSWebhook
        fields = ('transmit_account', 'webhook_id', 'webhook_name', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')
