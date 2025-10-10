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
    path('transmit-sms/reply-callback/<str:message_id>/', transmit_reply_callback, name='transmit_reply_callback'),
    
    # Account setup
    path('setup-transmit-account/', SetupTransmitAccountView.as_view(), name='setup_transmit_account'),
    #messages list
    path('sms-messages/', SMSMessageListView.as_view(), name='sms-message-list'),
    path("wallet/<str:location_id>/add-funds/", wallet_adjust_funds, name="wallet_add_funds"),
    path("dashboard/analytics/", DashboardAnalyticsView.as_view(), name="dashboard-analytics"),

    path("ghl-account/dashboard/", GHLAccountDashboardAPIView.as_view(), name="ghl-dashboard"),
    path("dashboard/", GHLAccountDashboardAPIView.as_view(), name="ghl-dashboard"),

    path("ghl-account/messages/", GHLAccountMessagesAPIView.as_view(), name="ghl-messages"),
    path("ghl-account/transactions/", GHLAccountTransactionsAPIView.as_view(), name="ghl-transactions"),

    path("numbers/", CombinedNumbersList.as_view(), name="combined_numbers_list"),
    path("numbers/<str:location_id>/", CombinedNumbersList.as_view(), name="combined_numbers_list_with_location"),
    path("numbers-register/", RegisterNumber.as_view(), name="register_number"),

    path('numbers/available/', GetAvailableNumbers.as_view(), name='available-numbers'),
    path('numbers/location/<str:location_id>/', GetLocationNumbers.as_view(), name='location-numbers'),
]