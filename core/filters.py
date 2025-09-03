import django_filters
from .models import Wallet, WalletTransaction


class WalletFilter(django_filters.FilterSet):
    balance_min = django_filters.NumberFilter(field_name="balance", lookup_expr="gte")
    balance_max = django_filters.NumberFilter(field_name="balance", lookup_expr="lte")
    location_name = django_filters.CharFilter(field_name="account__location_name", lookup_expr="icontains")
    ordering = django_filters.OrderingFilter(
        fields=(
            ("balance", "balance"),
            ("updated_at", "updated_at"),
        )
    )

    class Meta:
        model = Wallet
        fields = ["location_name"]


class WalletTransactionFilter(django_filters.FilterSet):
    start_date = django_filters.DateFilter(field_name="created_at", lookup_expr="gte")
    end_date = django_filters.DateFilter(field_name="created_at", lookup_expr="lte")
    transaction_type = django_filters.CharFilter(field_name="transaction_type")  # credit / debit
    wallet = django_filters.UUIDFilter(field_name="wallet__id")  # filter by wallet id
    ordering = django_filters.OrderingFilter(
        fields=(
            ("created_at", "created_at"),
            ("amount", "amount"),
        )
    )

    class Meta:
        model = WalletTransaction
        fields = ["transaction_type", "wallet"]
