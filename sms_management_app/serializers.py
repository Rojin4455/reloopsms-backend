from rest_framework import serializers
from django.db.models import Count, Q, Sum

from .models import GHLTransmitSMSMapping, SMSMessage, WebhookLog
from core.models import GHLAuthCredentials, Wallet, WalletTransaction, TransmitNumber

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



class RecentMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSMessage
        fields = [
            "id",
            "direction",
            "to_number",
            "from_number",
            "status",
            "message_content",
            "created_at",
        ]
        
# --- Shared serializers ---
class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "transaction_type", "amount", "balance_after", "description", "reference_id", "created_at"]

class SMSMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSMessage
        fields = [
            "id", "message_content", "to_number", "from_number",
            "direction", "status", "cost", "segments",
            "sent_at", "delivered_at", "created_at"
        ]

# --- Dashboard serializers ---




class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = ["id", "transaction_type", "amount", "balance_after", "description", "reference_id", "created_at"]

class SMSMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSMessage
        fields = [
            "id", "message_content", "to_number", "from_number",
            "direction", "status", "cost", "segments",
            "sent_at", "delivered_at", "created_at"
        ]

class WalletSerializer(serializers.ModelSerializer):
    recent_transactions = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    total_credits = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = ["balance", "inbound_segment_charge", "outbound_segment_charge",
                  "recent_transactions", "total_spent", "total_credits"]

    def get_recent_transactions(self, obj):
        transactions = obj.transactions.all().order_by("-created_at")[:5]
        return WalletTransactionSerializer(transactions, many=True).data

    def get_total_spent(self, obj):
        spent = obj.transactions.filter(transaction_type="debit").aggregate(total=Sum("amount"))["total"] or 0
        return float(spent)

    def get_total_credits(self, obj):
        credits = obj.transactions.filter(transaction_type="credit").aggregate(total=Sum("amount"))["total"] or 0
        return float(credits)

class MappingSerializer(serializers.ModelSerializer):
    transmit_account_name = serializers.CharField(source="transmit_account.account_name", read_only=True)

    class Meta:
        model = GHLTransmitSMSMapping
        fields = ["id", "transmit_account_name", "mapped_at"]

class MessagesSummarySerializer(serializers.Serializer):
    recent_messages = SMSMessageSerializer(many=True)
    total_sent = serializers.IntegerField()
    total_delivered = serializers.IntegerField()
    total_failed = serializers.IntegerField()
    outbound_count = serializers.IntegerField()
    inbound_count = serializers.IntegerField()

class AlertsSerializer(serializers.Serializer):
    low_balance = serializers.BooleanField()
    pending_messages = serializers.IntegerField()
    failed_messages = serializers.IntegerField()

class DashboardSerializer(serializers.Serializer):
    account = serializers.DictField()
    wallet = WalletSerializer()
    mapping = MappingSerializer()
    messages_summary = MessagesSummarySerializer()
    alerts = AlertsSerializer()




class TransmitNumberSerializer(serializers.ModelSerializer):
    location_id = serializers.SerializerMethodField()

    class Meta:
        model = TransmitNumber
        fields = ['id', 'number', 'status', 'location_id', 'price', 'is_active', 'purchased_at', 'registered_at', 'last_synced_at']

    def get_location_id(self, obj):
        return obj.ghl_account.id if obj.ghl_account else None