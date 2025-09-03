from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import GHLAuthCredentials, Wallet

@receiver(post_save, sender=GHLAuthCredentials)
def create_wallet_for_ghl_account(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(account=instance)
        print(f"💰 Wallet created for new GHL account {instance.user_id}")