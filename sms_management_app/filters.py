import django_filters
from .models import SMSMessage

class SMSMessageFilter(django_filters.FilterSet):
    # Date range filters
    created_at__gte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at__lte = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
    sent_at__gte = django_filters.DateTimeFilter(field_name="sent_at", lookup_expr="gte")
    sent_at__lte = django_filters.DateTimeFilter(field_name="sent_at", lookup_expr="lte")

    # Foreign key filters
    ghl_account = django_filters.CharFilter(field_name="ghl_account__id", lookup_expr="exact")
    transmitsms_account = django_filters.CharFilter(field_name="transmit_account__id", lookup_expr="exact")

    class Meta:
        model = SMSMessage
        fields = [
            "status",
            "direction",
            "to_number",
            "from_number",
            "ghl_account",
            "transmitsms_account",
        ]