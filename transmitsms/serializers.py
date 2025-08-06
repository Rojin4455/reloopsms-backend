from rest_framework import serializers

from .models import TransmitSMSAccount

class TransmitSMSAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransmitSMSAccount
        fields = '__all__'
        read_only_fields = ['id', 'created_at']
