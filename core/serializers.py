from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from core.models import GHLAuthCredentials, Wallet, WalletTransaction


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for User model
    """
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'date_joined')
        read_only_fields = ('id', 'date_joined')


class RegisterSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration
    """
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'password_confirm', 'first_name', 'last_name')
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError("Password fields didn't match.")
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user
    


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = [
            "id",
            "balance",
            "inbound_segment_charge",
            "outbound_segment_charge",
            "updated_at",
        ]
        read_only_fields = ["balance", "updated_at"]


class GHLAuthCredentialsSerializer(serializers.ModelSerializer):
    wallet = WalletSerializer()
    class Meta:
        model = GHLAuthCredentials
        exclude = ["access_token", "refresh_token", "expires_in"]
    
    def update(self, instance, validated_data):
        wallet_data = validated_data.pop("wallet", None)

        instance = super().update(instance, validated_data)

        if wallet_data:
            wallet, _ = Wallet.objects.get_or_create(account=instance)
            for field, value in wallet_data.items():
                setattr(wallet, field, value)
            wallet.save()

        return instance
    
class WalletSerializerForTransaction(serializers.ModelSerializer):
    account_user_id = serializers.CharField(source="account.user_id", read_only=True)
    company_id = serializers.CharField(source="account.company_id", read_only=True)
    location_name = serializers.CharField(source="account.location_name", read_only=True)

    class Meta:
        model = Wallet
        fields = [
            "id",
            "account_user_id",
            "company_id",
            "location_name",
            "balance",
            "inbound_segment_charge",
            "outbound_segment_charge",
            "updated_at",
        ]

class WalletTransactionSerializer(serializers.ModelSerializer):
    wallet = WalletSerializerForTransaction(read_only=True)
    account = serializers.CharField(source="wallet.account.user_id", read_only=True)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "wallet",
            "account",
            "transaction_type",
            "amount",
            "balance_after",
            "description",
            "reference_id",
            "created_at",
        ]
    
class WalletTransactionListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "transaction_type",
            "amount",
            "balance_after",
            "description",
            "reference_id",
            "created_at",
        ]


class WalletListingSerializer(serializers.ModelSerializer):
    account_user_id = serializers.CharField(source="account.user_id", read_only=True)
    company_id = serializers.CharField(source="account.company_id", read_only=True)
    location_name = serializers.CharField(source="account.location_name", read_only=True)
    location_id = serializers.CharField(source="account.location_id", read_only=True)
    transactions = serializers.SerializerMethodField()
    total_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "account_user_id",
            "company_id",
            "location_name",
            "location_id",
            "balance",
            "inbound_segment_charge",
            "outbound_segment_charge",
            "updated_at",
            "transactions",
            "total_transactions"
        ]
    def get_transactions(self, obj):
        recent_transactions = obj.transactions.order_by("-created_at")[:5]
        return WalletTransactionListingSerializer(recent_transactions, many=True).data
    
    def get_total_transactions(self, obj):
        return obj.transactions.count()