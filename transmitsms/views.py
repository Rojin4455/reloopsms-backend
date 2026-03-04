from rest_framework import viewsets

from .models import TransmitSMSAccount, TransmitSMSMMSWebhook
from .serializers import TransmitSMSAccountSerializer, TransmitSMSMMSWebhookSerializer


class TransmitSMSAccountViewSet(viewsets.ModelViewSet):
    queryset = TransmitSMSAccount.objects.all()
    serializer_class = TransmitSMSAccountSerializer


class TransmitSMSMMSWebhookViewSet(viewsets.ModelViewSet):
    queryset = TransmitSMSMMSWebhook.objects.all()
    serializer_class = TransmitSMSMMSWebhookSerializer
