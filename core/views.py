from django.shortcuts import render
from decouple import config
import requests
from django.http import JsonResponse
import json
from django.shortcuts import redirect, render
from core.models import GHLAuthCredentials
from django.views.decorators.csrf import csrf_exempt
from core.services import get_location_name
from urllib.parse import urlencode
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated,IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from .serializers import UserSerializer, RegisterSerializer
from rest_framework import generics
from .models import GHLAuthCredentials
from .serializers import GHLAuthCredentialsSerializer



# Create your views here.

GHL_CLIENT_ID = config("GHL_CLIENT_ID")
GHL_CLIENT_SECRET = config("GHL_CLIENT_SECRET")
GHL_REDIRECTED_URI = config("GHL_REDIRECTED_URI")
FRONTEND_URL = config("FRONTEND_URL")
TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
SCOPE = config("SCOPE")

def auth_connect(request):
    auth_url = ("https://marketplace.gohighlevel.com/oauth/chooselocation?response_type=code&"
                f"redirect_uri={GHL_REDIRECTED_URI}&"
                f"client_id={GHL_CLIENT_ID}&"
                f"scope={SCOPE}"
                )
    return redirect(auth_url)



def callback(request):
    
    code = request.GET.get('code')

    if not code:
        return JsonResponse({"error": "Authorization code not received from OAuth"}, status=400)

    return redirect(f'{config("BASE_URI")}/api/core/auth/tokens?code={code}')




from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
import json
from sms_management_app.services import GHLIntegrationService, TransmitSMSService
from core.models import GHLAuthCredentials
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
        mapping = service.setup_transmit_account_for_ghl(obj, account_details)
        query_params = urlencode({
            "locationId":response_data.get("locationId"),
        })

        frontend_url = f"{FRONTEND_URL}/highlevel-accounts?{query_params}"
        
        return redirect(frontend_url)
        
    except requests.exceptions.JSONDecodeError:
        frontend_url = "http://localhost:3000/admin/error-onboard"
        return redirect(frontend_url)
    



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
