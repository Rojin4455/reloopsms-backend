from rest_framework import serializers
from .models import GHLTransmitSMSMapping, SMSMessage
from core.models import GHLAuthCredentials

class GHLTransmitSMSMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = GHLTransmitSMSMapping
        fields = '__all__'
        
class GHLAuthCredentialsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GHLAuthCredentials
        fields = ["id", "location_name"]

class SMSMessageSerializer(serializers.ModelSerializer):
    ghl_account = GHLAuthCredentialsSerializer()
    class Meta:
        model = SMSMessage
        fields = '__all__'