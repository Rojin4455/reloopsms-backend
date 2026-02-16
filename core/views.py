import requests
import json
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from decouple import config
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import urlencode
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Sum, Count, Q
from decimal import Decimal
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated,IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import viewsets
from rest_framework import generics
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from core.models import GHLAuthCredentials
from core.services import get_location_name
from .serializers import UserSerializer, RegisterSerializer
from .models import GHLAuthCredentials, Wallet, WalletTransaction
from .serializers import GHLAuthCredentialsSerializer, WalletSerializer, WalletTransactionSerializer, WalletListingSerializer, WalletTransactionListingSerializer
from .filters import WalletFilter, WalletTransactionFilter

import stripe
from urllib.parse import quote
from django.utils.timezone import now
from .models import StripeCustomerData
from django.conf import settings


stripe.api_key = (
    settings.STRIPE_TEST_API_KEY if settings.DEBUG else settings.STRIPE_LIVE_API_KEY
)


# Create your views here.

GHL_CLIENT_ID = config("GHL_CLIENT_ID")
GHL_CLIENT_SECRET = config("GHL_CLIENT_SECRET")
GHL_REDIRECTED_URI = config("GHL_REDIRECTED_URI")
FRONTEND_URL = config("FRONTEND_URL")
TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
SCOPE = config("SCOPE")



AGENCY_CLIENT_ID = config("AGENCY_CLIENT_ID")
AGENCY_CLIENT_SECRET = config("AGENCY_CLIENT_SECRET")
AGENCY_REDIRECT_URI = config("AGENCY_REDIRECT_URI")
AGENCY_SCOPE = config("AGENCY_SCOPE")


def auth_connect(request):
    auth_url = ("https://marketplace.gohighlevel.com/oauth/chooselocation?response_type=code&"
                f"redirect_uri={GHL_REDIRECTED_URI}&"
                f"client_id={GHL_CLIENT_ID}&"
                f"scope={SCOPE}"
                )
    return redirect(auth_url)


def agency_auth_connect(request):
    auth_url = (
        "https://marketplace.gohighlevel.com/oauth/chooselocation?"
        f"response_type=code&"
        f"redirect_uri={AGENCY_REDIRECT_URI}&"
        f"client_id={AGENCY_CLIENT_ID}&"
        f"scope={AGENCY_SCOPE}"
    )
    return redirect(auth_url)


def callback(request):
    
    code = request.GET.get('code')

    if not code:
        return JsonResponse({"error": "Authorization code not received from OAuth"}, status=400)

    return redirect(f'{config("BASE_URI")}/api/core/auth/tokens?code={code}')



def agency_callback(request):
    
    code = request.GET.get('code')

    if not code:
        return JsonResponse({"error": "Authorization code not received from OAuth"}, status=400)

    return redirect(f'{config("BASE_URI")}/api/core/auth/agency-tokens?code={code}')




from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
import json
from sms_management_app.services import GHLIntegrationService, TransmitSMSService
from core.models import GHLAuthCredentials, AgencyToken
from django.utils import timezone
from sms_management_app.utils import format_password

def tokens(request):
    authorization_code = request.GET.get("code")

    if not authorization_code:
        return JsonResponse({"error": "Authorization code not found"}, status=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": GHL_CLIENT_ID,
        "client_secret": GHL_CLIENT_SECRET,
        "redirect_uri": GHL_REDIRECTED_URI,
        "code": authorization_code,
    }

    response = requests.post(TOKEN_URL, data=data)

    try:
        response_data = response.json()
        if not response_data:
            return
        print("response.data: ", response_data)
        if not response_data.get('access_token'):
            return render(request, 'onboard.html', context={
                "message": "Invalid JSON response from API",
                "status_code": response.status_code,
                "response_text": response.text[:400]
            }, status=400)
        

        data = get_location_name(location_id=response_data.get("locationId"), access_token=response_data.get('access_token'))
        location_data = data.get("location")
        obj, created = GHLAuthCredentials.objects.update_or_create(
            location_id= response_data.get("locationId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "company_id": response_data.get("companyId"),
                "user_id":response_data.get("userId"),
                "location_name":location_data.get("name"),
                "timezone": location_data.get("timezone"),
                "business_email":location_data.get("email"),
                "business_phone":location_data.get("phone")
            }
        )
        password = format_password(obj.location_name)
        print("password: ", password)

        account_details = {
            'name': obj.location_name,
            'email': obj.business_email,
            'phone': obj.business_phone,
            'password': password
        }

        print("changes updates")
        
        # # Setup TransmitSMS account
        service = GHLIntegrationService()
        error_message = service.setup_transmit_account_for_ghl(obj, account_details)

        query_params = {
            "locationId": response_data.get("locationId"),
        }

        if error_message:
            query_params["warning"] = error_message

        frontend_url = f"{FRONTEND_URL}/highlevel-accounts?{urlencode(query_params)}"
        
        return redirect(frontend_url)
        
    except requests.exceptions.JSONDecodeError:
        frontend_url = "http://localhost:3000/admin/error-onboard"
        return redirect(frontend_url)
    


def agency_tokens(request):
    authorization_code = request.GET.get("code")

    if not authorization_code:
        return JsonResponse({"error": "Authorization code not found"}, status=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": AGENCY_CLIENT_ID,
        "client_secret": AGENCY_CLIENT_SECRET,
        "redirect_uri": AGENCY_REDIRECT_URI,
        "code": authorization_code,
    }

    

    response = requests.post(TOKEN_URL, data=data)

    try:
        response_data = response.json()
        print("response_data",response_data)
        if not response_data or not response_data.get("access_token"):
            return render(request, 'onboard.html', context={
                "message": "Invalid token response from API",
                "status_code": response.status_code,
                "response_text": response.text[:400],
            }, status=400)

        # Save or update agency token
        obj, created = AgencyToken.objects.update_or_create(
            company_id=response_data.get("companyId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "user_id": response_data.get("userId"),
                "is_bulk_installation": response_data.get("isBulkInstallation", False),
                "token_type": response_data.get("token_type", "Bearer"),
                "refresh_token_id": response_data.get("refreshTokenId"),
            }
        )

        print(f"✅ Agency token saved for company {obj.company_id}")

        # Redirect to frontend success page
        query_params = urlencode({
            "companyId": obj.company_id,
            "userId": obj.user_id
        })
        frontend_url = f"{FRONTEND_URL}/agency-accounts?{query_params}"
        return redirect(frontend_url)

    except requests.exceptions.JSONDecodeError:
        return redirect(f"{FRONTEND_URL}/admin/error-onboard")


@method_decorator(csrf_exempt, name='dispatch')
class SetupTransmitAccountView(View):
    """Setup TransmitSMS account for GHL location"""
    
    def post(self, request):
        try:
            data = json.loads(request.body)
            location_id = data.get('location_id')
            account_details = {
                'name': data.get('account_name'),
                'email': data.get('email'),
                'phone': data.get('phone'),
                'password': data.get('password', 'default_password_123')
            }
            
            # Get GHL account
            try:
                ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)
            except GHLAuthCredentials.DoesNotExist:
                return JsonResponse({
                    "error": "GHL account not found"
                }, status=404)
            
            # Setup TransmitSMS account
            service = GHLIntegrationService()
            mapping = service.setup_transmit_account_for_ghl(ghl_account, account_details)
            
            return JsonResponse({
                "message": "TransmitSMS account setup successfully",
                "mapping_id": str(mapping.id),
                "transmit_account_id": mapping.transmit_account.account_id
            }, status=200)
            
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)




class LogoutView(APIView):
    """
    Logout user by blacklisting the refresh token
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            refresh_token = request.data["refresh_token"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({
                'message': 'Successfully logged out'
            }, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response({
                'error': 'Invalid token'
            }, status=status.HTTP_400_BAD_REQUEST)




class GHLAuthCredentialsListView(generics.ListAPIView):
    """
    GET /api/ghl-auth-credentials/ → List all GHL credentials
    """
    queryset = GHLAuthCredentials.objects.all()
    serializer_class = GHLAuthCredentialsSerializer
    permission_classes = [IsAdminUser]


class GHLAuthCredentialsDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/ghl-auth-credentials/<int:pk>/ → Retrieve one
    PUT    /api/ghl-auth-credentials/<int:pk>/ → Update
    DELETE /api/ghl-auth-credentials/<int:pk>/ → Delete
    """
    queryset = GHLAuthCredentials.objects.all()
    serializer_class = GHLAuthCredentialsSerializer
    permission_classes = [IsAdminUser]

ORDERS_WEBHOOK_URL = "https://ttillpgzclaggdureeka.supabase.co/functions/v1/orders-webhook"


@csrf_exempt
def orders_webhook(request):
    """
    Webhook that forwards incoming payload to the external orders webhook
    and returns the external webhook response as-is.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        # Forward the same body we received
        body = request.body
        content_type = request.content_type or "application/json"
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        resp = requests.post(
            ORDERS_WEBHOOK_URL,
            data=body,
            headers=headers,
            timeout=30,
        )
        # Return the same status and body we got from the external webhook
        response = HttpResponse(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("Content-Type", "application/json"),
        )
        return response
    except requests.RequestException as e:
        return JsonResponse(
            {"error": "External webhook request failed", "detail": str(e)},
            status=502,
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def webhook_handler(request):
    if request.method != "POST":
        return JsonResponse({"message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        print("date:----- ", data)
        # WebhookLog.objects.create(data=data)
        # event_type = data.get("type")
        # handle_webhook_event.delay(data, event_type)
        return JsonResponse({"message":"Webhook received"}, status=200)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

class WalletViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only Wallet API for admin"""
    queryset = Wallet.objects.select_related("account")
    serializer_class = WalletSerializer

    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        """Get all transactions for a given wallet"""
        wallet = self.get_object()
        transactions = WalletTransaction.objects.filter(wallet=wallet)
        serializer = WalletTransactionSerializer(transactions, many=True)
        return Response(serializer.data)


class WalletTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """List all transactions (with filtering support)"""
    queryset = WalletTransaction.objects.select_related("wallet", "wallet__account")
    serializer_class = WalletTransactionSerializer


class WalletListingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Wallet.objects.all().select_related("account")
    serializer_class = WalletListingSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_class = WalletFilter
    ordering_fields = ["balance", "updated_at"]
    ordering = ["-updated_at"]
    search_fields = [
        "account__location_name",
        "account__business_email",
        "account__business_phone",
        "account__contact_name",
    ]


class WalletTransactionListingViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WalletTransaction.objects.all().select_related("wallet", "wallet__account")
    serializer_class = WalletTransactionSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_class = WalletTransactionFilter
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]
    search_fields = [
        "wallet__account__location_name",
        "wallet__account__business_email",
        "wallet__account__business_phone",
        "wallet__account__contact_name",
    ]

class WalletSummaryView(APIView):
    def get(self, request, *args, **kwargs):
        qs = Wallet.objects.all()

        summary_data = {
            "total_accounts": qs.count(),
            "total_balance": qs.aggregate(total=Sum("balance"))["total"] or 0,
            "total_credits": WalletTransaction.objects.filter(transaction_type="credit").aggregate(total=Sum("amount"))["total"] or 0,
            "total_debits": WalletTransaction.objects.filter(transaction_type="debit").aggregate(total=Sum("amount"))["total"] or 0,
            "total_transactions": WalletTransaction.objects.count(),
        }
        return Response(summary_data, status=200)





@csrf_exempt
def stripe_customer_lookup(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        data = json.loads(request.body)
        email = data.get("email")
        agency = AgencyToken.objects.first()
        token = agency.access_token

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)
        if not token:
            return JsonResponse({"error": "Token is required"}, status=400)

        # --- 1️⃣ Search for Stripe customer ---
        customers = stripe.Customer.search(
            query=f"email:'{email}'",
            limit=10,
        )

        if not customers.data:
            return JsonResponse({"message": "No customers found for this email."}, status=404)

        # Pick the latest customer by created date
        latest_customer = sorted(customers.data, key=lambda c: c.created, reverse=True)[0]
        customer_id = latest_customer.id

        # --- 2️⃣ Get default payment method ---
        payment_method_id = None
        try:
            payment_methods = stripe.PaymentMethod.list(
                customer=customer_id,
                type="card",
                limit=1,
            )
            if payment_methods.data:
                payment_method_id = payment_methods.data[0].id
        except Exception:
            pass

        # --- 3️⃣ Lookup LeadConnector Location ---
        encoded_email = quote(email)
        url = f"https://services.leadconnectorhq.com/locations/search?email={encoded_email}"

        headers = {
            "Accept": "application/json",
            "Version": "2021-07-28",
            "Authorization": f"Bearer {token}",
        }

        location_id = None
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            location_data = response.json()

            if "locations" in location_data and location_data["locations"]:
                # Sort by createdAt if available, else take the first
                latest_location = location_data["locations"][0]
                location_id = latest_location.get("id")
        except Exception as e:
            print("LeadConnector lookup failed:", str(e))

        # --- 4️⃣ Save or update in DB ---
        obj, created = StripeCustomerData.objects.update_or_create(
            email=email,
            defaults={
                "customer_id": customer_id,
                "payment_method_id": payment_method_id,
                "location_id": location_id,
            },
        )

        # --- 5️⃣ Return combined result ---
        return JsonResponse({
            "success": True,
            "message": "Customer and location saved successfully.",
            "email": obj.email,
            "customer_id": obj.customer_id,
            "payment_method_id": obj.payment_method_id,
            "location_id": obj.location_id,
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    


import re

@csrf_exempt
def create_deduction(request):
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        # data = json.loads(request.body)
        # location_id = data.get("location_id")
        # amount = data.get("amount")  # Amount in cents
        # currency = data.get("currency", "usd")  # default to USD

        data = json.loads(request.body)

        # Extract location_id
        location_id = data.get("SMS Recharge LocationID")

        # Extract the credit amount string
        recharge_text = data.get("SMS Credit Recharge", "")

        # Find all dollar amounts (like $30.00 and $1)
        amounts = re.findall(r"\$([\d\.]+)", recharge_text)

        # Convert and sum them up (30.00 + 1.00 = 31.00)
        amount = sum(float(a) for a in amounts) if amounts else 0.0

        # Default currency
        currency = "usd"

        if not location_id or not amount:
            return JsonResponse({"error": "location_id and amount are required"}, status=400)

        # 1️⃣ Lookup StripeCustomer by location_id
        customer = StripeCustomerData.objects.filter(location_id=location_id).first()
        if not customer:
            return JsonResponse({"error": "Customer not found for this location_id"}, status=404)

        if not customer.payment_method_id:
            return JsonResponse({"error": "Customer has no saved payment method"}, status=400)

        # 2️⃣ Create PaymentIntent (charge saved card)
        payment_intent = stripe.PaymentIntent.create(
            amount=int(float(amount) * 100),
            currency=currency,
            customer=customer.customer_id,
            payment_method=customer.payment_method_id,
            off_session=True,
            confirm=True,
        )

        amount = Decimal(str(amount))
        reference_id = payment_intent.id

        if amount <= 0:
            return JsonResponse({"error": "Invalid amount"}, status=400)

        try:
            account = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return JsonResponse({"error": "GHL account not found"}, status=404)

        wallet, _ = Wallet.objects.get_or_create(account=account)


        new_balance = wallet.add_funds(amount,reference_id=reference_id)

        # 3️⃣ Return result
        return JsonResponse({
            "success": True,
            "message": "Payment completed successfully.",
            "payment_intent_id": payment_intent.id,
            "status": payment_intent.status,
            "amount": payment_intent.amount,
            "currency": payment_intent.currency,
            "customer_email": customer.email,
            "location_id": location_id,
        })

    except stripe.error.CardError as e:
        # Handle declined card, SCA required, etc.
        err = e.json_body.get("error", {})
        return JsonResponse({
            "success": False,
            "message": err.get("message"),
            "code": err.get("code")
        }, status=402)

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)