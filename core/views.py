import requests
import json
import logging
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
from core.service import GHLService
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

logger = logging.getLogger(__name__)


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


def _lookup_ghl_contact_id_by_email(access_token: str, location_id: str, email: str):
    """
    Resolve a GHL contact by email within a location and return contact ID.
    Tries duplicate-search endpoint first, then generic contacts query as fallback.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }

    # 1) Preferred duplicate lookup endpoint
    try:
        dup_resp = requests.post(
            "https://services.leadconnectorhq.com/contacts/search/duplicate",
            json={"locationId": location_id, "email": email},
            headers=headers,
            timeout=20,
        )
        if dup_resp.ok:
            dup_data = dup_resp.json()
            contact = dup_data.get("contact") or {}
            contact_id = contact.get("id") or contact.get("_id")
            if contact_id:
                return contact_id
    except Exception:
        pass

    # 2) Fallback query endpoint
    try:
        query_resp = requests.get(
            f"https://services.leadconnectorhq.com/contacts/?locationId={quote(location_id)}&query={quote(email)}",
            headers=headers,
            timeout=20,
        )
        if query_resp.ok:
            query_data = query_resp.json()
            contacts = query_data.get("contacts") or []
            for contact in contacts:
                contact_email = (contact.get("email") or "").strip().lower()
                if contact_email == email.strip().lower():
                    return contact.get("id") or contact.get("_id")
    except Exception:
        pass

    return None


def _lookup_latest_stripe_customer_id(email: str):
    customers = stripe.Customer.search(
        query=f"email:'{email}'",
        limit=10,
    )
    print("customers: ",customers)
    if not customers.data:
        return None
    latest_customer = sorted(customers.data, key=lambda c: c.created, reverse=True)[0]
    return latest_customer.id


def _get_main_location_credentials():
    main_location_id = settings.GHL_MAIN_LOCATION_ID
    try:
        creds = GHLAuthCredentials.objects.get(location_id=main_location_id)
    except GHLAuthCredentials.DoesNotExist:
        return None, f"Main GHL account not found for location_id={main_location_id}"
    if not creds.access_token:
        return None, f"Main GHL account is missing access token for location_id={main_location_id}"
    return creds, None


def auth_connect(request):
    from core.ghl_auth import build_location_oauth_url

    return redirect(build_location_oauth_url())


def agency_auth_connect(request):
    from core.ghl_auth import build_agency_oauth_url

    return redirect(build_agency_oauth_url())


def callback(request):
    from core.ghl_auth import parse_location_ids_from_query_params

    code = request.GET.get("code")

    if not code:
        return JsonResponse({"error": "Authorization code not received from OAuth"}, status=400)

    callback_params = {key: request.GET.getlist(key) for key in request.GET.keys()}
    print(f"[GHL OAuth] callback query params: {callback_params}")
    logger.info("[GHL OAuth] callback query params: %s", callback_params)

    location_ids = parse_location_ids_from_query_params(request.GET)
    print(f"[GHL OAuth] callback parsed location_ids: {location_ids}")
    logger.info("[GHL OAuth] callback parsed location_ids: %s", location_ids)

    query_pairs = [("code", code)]
    if len(location_ids) == 1:
        query_pairs.append(("locationId", location_ids[0]))
    elif len(location_ids) > 1:
        query_pairs.append(("locationIds", ",".join(location_ids)))

    return redirect(f'{config("BASE_URI")}/api/core/auth/tokens?{urlencode(query_pairs)}')



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
    from core.ghl_auth import exchange_location_oauth_code, parse_location_ids_from_query_params

    authorization_code = request.GET.get("code")

    if not authorization_code:
        return JsonResponse({"error": "Authorization code not found"}, status=400)

    token_params = {key: request.GET.getlist(key) for key in request.GET.keys()}
    print(f"[GHL OAuth] tokens step query params: {token_params}")
    logger.info("[GHL OAuth] tokens step query params: %s", token_params)

    preferred_location_ids = parse_location_ids_from_query_params(request.GET)
    preferred_location_id = preferred_location_ids[0] if len(preferred_location_ids) == 1 else None

    result, error = exchange_location_oauth_code(
        authorization_code,
        preferred_location_id=preferred_location_id,
        preferred_location_ids=preferred_location_ids,
    )
    if error:
        query_params = {"warning": str(error)}
        return redirect(f"{FRONTEND_URL}/highlevel-accounts?{urlencode(query_params)}")

    credentials = result["credentials"]
    skipped_locations = result.get("skipped_locations") or []
    transmit_warnings = []
    service = GHLIntegrationService()

    for obj, _created in credentials:
        password = format_password(obj.location_name)
        account_details = {
            "name": obj.location_name,
            "email": obj.business_email,
            "phone": obj.business_phone,
            "password": password,
        }
        error_message = service.setup_transmit_account_for_ghl(obj, account_details)
        if error_message:
            transmit_warnings.append(f"{obj.location_name or obj.location_id}: {error_message}")

    query_params = {}
    if len(credentials) == 1:
        query_params["locationId"] = credentials[0][0].location_id

    messages = []
    if len(credentials) > 1:
        messages.append(f"Connected {len(credentials)} HighLevel accounts.")
    if skipped_locations:
        messages.append(
            f"Skipped {len(skipped_locations)} inactive sub-account"
            f"{'' if len(skipped_locations) == 1 else 's'}."
        )
    if transmit_warnings:
        messages.extend(transmit_warnings)

    if messages:
        query_params["warning"] = " ".join(messages)

    redirect_url = f"{FRONTEND_URL}/highlevel-accounts"
    if query_params:
        redirect_url = f"{redirect_url}?{urlencode(query_params)}"
    return redirect(redirect_url)
    


def agency_tokens(request):
    from core.ghl_auth import exchange_agency_oauth_code
    import logging

    logger = logging.getLogger(__name__)
    authorization_code = request.GET.get("code")

    if not authorization_code:
        return JsonResponse({"error": "Authorization code not found"}, status=400)

    result, error = exchange_agency_oauth_code(authorization_code)
    if error:
        return JsonResponse({"error": str(error)}, status=400)

    obj, _created = result
    logger.info("Agency token saved for company %s", obj.company_id)

    query_params = urlencode({
        "companyId": obj.company_id,
        "userId": obj.user_id,
    })
    return redirect(f"{FRONTEND_URL}/agency-accounts?{query_params}")


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
    queryset = GHLAuthCredentials.objects.select_related(
        "wallet",
        "transmit_sms_mapping__transmit_account",
    ).order_by("location_name", "id")
    serializer_class = GHLAuthCredentialsSerializer
    permission_classes = [IsAdminUser]

    def list(self, request, *args, **kwargs):
        from transmitsms.models import TransmitAgencyBalance

        response = super().list(request, *args, **kwargs)
        agency = TransmitAgencyBalance.get_snapshot()
        response.data["transmit_agency_balance"] = {
            "balance": agency.balance,
            "currency": agency.currency,
            "synced_at": agency.synced_at,
        }
        return response


class GHLAuthCredentialsDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/ghl-auth-credentials/<int:pk>/ → Retrieve one
    PUT    /api/ghl-auth-credentials/<int:pk>/ → Update
    DELETE /api/ghl-auth-credentials/<int:pk>/ → Delete
    """
    queryset = GHLAuthCredentials.objects.all()
    serializer_class = GHLAuthCredentialsSerializer
    permission_classes = [IsAdminUser]

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        save_kwargs = {}
        sync_result = None

        if "ghl_contact_email" in serializer.validated_data:
            contact_email = (serializer.validated_data.get("ghl_contact_email") or "").strip()
            if not contact_email:
                save_kwargs["ghl_contact_id"] = None
                sync_result = {"status": "cleared", "message": "ghl_contact_id cleared because email is empty"}
            else:
                main_creds, creds_error = _get_main_location_credentials()
                if creds_error:
                    return Response(
                        {"error": creds_error},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                contact_id = _lookup_ghl_contact_id_by_email(
                    main_creds.access_token,
                    settings.GHL_MAIN_LOCATION_ID,
                    contact_email,
                )
                if not contact_id:
                    return Response(
                        {"error": "No GHL contact found for the provided email in the main Reloop Pro account"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                save_kwargs["ghl_contact_id"] = contact_id

                stripe_customer_id = _lookup_latest_stripe_customer_id(contact_email)
                if not stripe_customer_id:
                    return Response(
                        {"error": "No Stripe customer found for the provided email"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                service = GHLService(access_token=main_creds.access_token)
                service.update_contact_custom_field(
                    contact_id=contact_id,
                    custom_field_id=settings.GHL_CF_STRIPE_ID,
                    field_value=stripe_customer_id,
                )

                location_field_id = getattr(settings, "GHL_CF_SMS_RECHARGE_LOCATION_ID", None)
                if location_field_id and instance.location_id:
                    service.update_contact_custom_field(
                        contact_id=contact_id,
                        custom_field_id=location_field_id,
                        field_value=instance.location_id,
                    )

                sync_result = {
                    "status": "updated",
                    "ghl_contact_id": contact_id,
                    "stripe_customer_id": stripe_customer_id,
                    "location_id": instance.location_id,
                }

        serializer.save(**save_kwargs)
        response_data = serializer.data
        if sync_result is not None:
            response_data["ghl_contact_sync"] = sync_result
        return Response(response_data, status=status.HTTP_200_OK)

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