from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.views import *
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

router = DefaultRouter()
router.register(r"wallets", WalletViewSet, basename="wallet")
router.register(r"transactions", WalletTransactionViewSet, basename="transaction")

router.register(r"wallets-list", WalletListingViewSet, basename="wallets")
router.register(r"transactions-list", WalletTransactionListingViewSet, basename="transactions")


urlpatterns = [
    path("auth/connect/", auth_connect, name="oauth_connect"),
    path("auth/tokens/", tokens, name="oauth_tokens"),
    path("auth/callback", callback, name="oauth_callback"),

    path("auth/agency-connect/", agency_auth_connect, name="oauth_connect"),
    path("auth/agency-tokens/", agency_tokens, name="oauth_tokens"),
    path("auth/agency-callback", agency_callback, name="oauth_callback"),


    path('login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    
    # Custom endpoints
    path('logout/', LogoutView.as_view(), name='logout'),
    path('ghl-auth-credentials/', GHLAuthCredentialsListView.as_view(), name='ghl-auth-credentials-list'),
    path('ghl-auth-credentials/<str:pk>/', GHLAuthCredentialsDetailView.as_view(), name='ghl-auth-credentials-detail'),
    path('test-provider', webhook_handler, name='test-provider'),

    path('wallet-summary/', WalletSummaryView.as_view()),

    path("", include(router.urls)),

    path("stripe/webhook/customer-lookup/", stripe_customer_lookup, name="stripe_customer_lookup"),
    path("stripe/webhook/create-deduction/", create_deduction, name="create_deduction"),
]