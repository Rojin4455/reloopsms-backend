from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import TransmitSMSAccountViewSet, TransmitSMSMMSWebhookViewSet
from sms_management_app.views import transmit_dlr_callback

router = DefaultRouter()
router.register(r'accounts', TransmitSMSAccountViewSet)
router.register(r'mms-webhooks', TransmitSMSMMSWebhookViewSet, basename='mms-webhook')

urlpatterns = [
    path('', include(router.urls)),
    # DLR webhook for MMS (and SMS) - TransmitSMS sends to /api/transmit-sms/dlr-callback/
    path('dlr-callback/', transmit_dlr_callback),
]


