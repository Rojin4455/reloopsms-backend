from rest_framework import serializers
from .models import GHLTransmitSMSMapping

class GHLTransmitSMSMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = GHLTransmitSMSMapping
        fields = '__all__'