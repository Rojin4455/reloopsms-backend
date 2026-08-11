from django.contrib import admin
from core.models import Wallet, WalletAutoRecharge

admin.site.register(Wallet)


@admin.register(WalletAutoRecharge)
class WalletAutoRechargeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "location_id",
        "status",
        "attempt_count",
        "charge_amount",
        "credit_amount",
        "last_failure_code",
        "next_retry_at",
        "created_at",
    )
    list_filter = ("status", "failure_category")
    search_fields = ("location_id", "ghl_contact_email", "last_payment_intent_id")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)
