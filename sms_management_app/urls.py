from django.urls import path
from sms_management_app.views import *

urlpatterns = [
    # path('ghl-conversation-webhook/', conversation_webhook_handler, name='ghl-conversation-webhook/'),
    path('mappings/', MappingListCreateView.as_view(), name='mapping-list-create'),
    path('mappings/<uuid:pk>/', MappingDetailView.as_view(), name='mapping-detail'),
    path('mappings/unmap/by-ghl/<str:ghl_id>/', UnmapByGHLView.as_view(), name='unmap-ghl'),
    path('mappings/unmap/by-transmit/<str:transmit_id>/', UnmapByTransmitView.as_view(), name='unmap-transmit'),

    

    path('ghl-conversation-webhook/', ghl_webhook_handler, name='ghl_webhook'),
    
    # TransmitSMS callbacks  
    path('transmit-sms/dlr-callback/', transmit_dlr_callback, name='transmit_dlr_callback'),
    path('transmit-sms/reply-callback/', transmit_reply_callback, name='transmit_reply_callback'),
    
    # Account setup
    path('setup-transmit-account/', SetupTransmitAccountView.as_view(), name='setup_transmit_account'),
]