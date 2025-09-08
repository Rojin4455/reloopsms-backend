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


class DashboardAnalyticsSerializer(serializers.Serializer):
    # SMS Stats
    total_messages = serializers.IntegerField()
    outbound_messages = serializers.IntegerField()
    inbound_messages = serializers.IntegerField()
    delivered_messages = serializers.IntegerField()
    failed_messages = serializers.IntegerField()
    delivery_rate = serializers.FloatField()
    
    # Financial Stats
    total_balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_spent = serializers.DecimalField(max_digits=10, decimal_places=2)
    avg_message_cost = serializers.DecimalField(max_digits=6, decimal_places=4)
    
    # Account Stats
    total_accounts = serializers.IntegerField()
    active_mappings = serializers.IntegerField()
    
    # Recent Activity
    recent_messages_24h = serializers.IntegerField()
    recent_transactions_24h = serializers.IntegerField()