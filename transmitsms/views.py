from rest_framework import viewsets

from .models import TransmitSMSAccount
from .serializers import TransmitSMSAccountSerializer


class TransmitSMSAccountViewSet(viewsets.ModelViewSet):
    queryset = TransmitSMSAccount.objects.all()
    serializer_class = TransmitSMSAccountSerializer
