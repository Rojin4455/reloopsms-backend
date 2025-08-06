from django.urls import path
from sms_management_app.views import *

urlpatterns = [
    path('ghl-conversation-webhook/', conversation_webhook_handler, name='ghl-conversation-webhook/'),
]