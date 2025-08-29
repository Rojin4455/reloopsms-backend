from django.shortcuts import render
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import GHLTransmitSMSMapping
from core.models import GHLAuthCredentials, Wallet
from transmitsms.models import TransmitSMSAccount
from .serializers import GHLTransmitSMSMappingSerializer, SMSMessageSerializer
from .tasks import update_ghl_message_status_task, urgent_update_ghl_message_status
from django.core.exceptions import ValidationError

# Create your views here.
@csrf_exempt
def conversation_webhook_handler(request):
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
    



class MappingListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/mappings/         → List all mappings
    POST /api/mappings/         → Create new mapping
    """
    queryset = GHLTransmitSMSMapping.objects.all()
    serializer_class = GHLTransmitSMSMappingSerializer


class MappingDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/mappings/<id>/ → Get a mapping
    PUT    /api/mappings/<id>/ → Update a mapping
    DELETE /api/mappings/<id>/ → Delete (unmap) a mapping
    """
    queryset = GHLTransmitSMSMapping.objects.all()
    serializer_class = GHLTransmitSMSMappingSerializer


class UnmapByGHLView(APIView):
    """
    DELETE /api/mappings/unmap/by-ghl/<ghl_id>/
    """
    def delete(self, request, ghl_id):
        try:
            mapping = GHLTransmitSMSMapping.objects.get(ghl_account__id=ghl_id)
            mapping.delete()
            return Response({"detail": "Mapping removed"}, status=status.HTTP_204_NO_CONTENT)
        except GHLTransmitSMSMapping.DoesNotExist:
            return Response({"error": "Mapping not found"}, status=status.HTTP_404_NOT_FOUND)


class UnmapByTransmitView(APIView):
    """
    DELETE /api/mappings/unmap/by-transmit/<transmit_id>/
    """
    def delete(self, request, transmit_id):
        try:
            mapping = GHLTransmitSMSMapping.objects.get(transmit_account__id=transmit_id)
            mapping.delete()
            return Response({"detail": "Mapping removed"}, status=status.HTTP_204_NO_CONTENT)
        except GHLTransmitSMSMapping.DoesNotExist:
            return Response({"error": "Mapping not found"}, status=status.HTTP_404_NOT_FOUND)




from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
import json
from .services import GHLIntegrationService, TransmitSMSService,update_ghl_message_status
from .models import WebhookLog, SMSMessage, GHLTransmitSMSMapping
from core.models import GHLAuthCredentials
from django.utils import timezone


@csrf_exempt
def ghl_webhook_handler(request):
    """Handle incoming webhooks from GoHighLevel"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        print("GHL Webhook Data:", data)
        
        # Log webhook
        WebhookLog.objects.create(
            webhook_type='ghl_inbound',
            raw_data=data
        )
        
        # Process message if it's an SMS
        if data.get('type') == 'SMS':
            service = GHLIntegrationService()
            result = service.process_ghl_message(data)
            
            if result['success']:
                return JsonResponse({
                    "message": "SMS sent successfully",
                    "message_id": str(result['message_id'])
                }, status=200)
            else:
                return JsonResponse({
                    "error": "Failed to send SMS",
                    "details": result['error']
                }, status=500)
        
        return JsonResponse({"message": "Webhook received"}, status=200)
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def transmit_dlr_callback(request):
    """Handle delivery receipt callbacks from TransmitSMS"""
    
    try:
        if request.method == "GET":
            # Data comes via query params
            data = request.GET.dict()
            print("TransmitSMS DLR (GET):", data)
        
        elif request.method == "POST":
            # Handle JSON or form data in POST
            try:
                data = json.loads(request.body)
                print("TransmitSMS DLR (POST JSON):", data)
            except json.JSONDecodeError:
                data = request.POST.dict()
                print("TransmitSMS DLR (POST Form):", data)
        else:
            return JsonResponse({"error": "Method not allowed"}, status=405)

        # Log webhook
        WebhookLog.objects.create(
            webhook_type='transmit_dlr',
            raw_data=data
        )
        
        # Extract message ID and status
        message_id = data.get('message_id')
        status = data.get('status', '').lower()

        try:
            sms_message = SMSMessage.objects.get(transmit_message_id=message_id)
            sms_message.delivery_status = json.dumps(data)

            # Map TransmitSMS status to our status
            if status in ['delivered', 'success']:
                sms_message.status = 'delivered'
                sms_message.delivered_at = timezone.now()
            elif status in ['failed', 'error', 'hard-bounce', 'soft-bounce']:
                sms_message.status = 'failed'
                sms_message.error_message = data.get('error_description', 'Delivery failed')
            elif status == 'expired':
                sms_message.status = 'expired'

            sms_message.save()


            
            update_ghl_message_status_task.delay(
                    message_id=sms_message.ghl_message_id,
                    status=sms_message.status,
                    ghl_token=sms_message.ghl_account.access_token,
                    sms_message_id=sms_message.id
                )
            
            
        except SMSMessage.DoesNotExist:
            print(f"SMS message not found for TransmitSMS ID: {message_id}")

        return JsonResponse({"message": "DLR processed"}, status=200)
    
    except Exception as e:
        print(f"DLR callback error: {e}")
        return JsonResponse({"error": str(e)}, status=500)



import requests
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .models import WebhookLog, GHLTransmitSMSMapping, SMSMessage, TransmitSMSAccount
from django.shortcuts import get_object_or_404

from django.utils.dateparse import parse_datetime

@csrf_exempt
def transmit_reply_callback(request, message_id):
    """Handle incoming SMS replies from TransmitSMS (GET webhook)"""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = request.GET.dict()
        print("📩 Reply webhook received:", data)

        # Example incoming data
        # {
        #   'user_id': '197820',
        #   'rate': '10',
        #   'mobile': '61414829252',
        #   'response': 'Hi there',
        #   'message_id': '1434767692',
        #   'response_id': '132669109',
        #   'longcode': '61430253994',
        #   'datetime_entry': '2025-08-14 12:33:18',
        #   'is_optout': 'no'
        # }

        # 1. Find the matching outbound SMS record
        sms_msg = get_object_or_404(SMSMessage, ghl_message_id=message_id)

        # 2. Get GHL credentials + conversation info
        ghl_creds = sms_msg.ghl_account
        access_token = ghl_creds.access_token
        conversation_id = sms_msg.ghl_conversation_id
        conversation_provider_id = ghl_creds.company_id   # <-- assuming this is correct

        if not conversation_id or not conversation_provider_id:
            return JsonResponse({"error": "Missing GHL IDs"}, status=400)
        
        # Create inbound SMSMessage first
        inbound_sms = SMSMessage.objects.create(
            ghl_account=ghl_creds,
            transmit_account=sms_msg.transmit_account,
            message_content=data.get("response"),
            to_number=data.get("longcode"),   # your business number
            from_number=data.get("mobile"),   # customer number
            direction="inbound",
            ghl_conversation_id=conversation_id,
            ghl_contact_id=sms_msg.ghl_contact_id,
            status="pending",   # will update after charging
            transmit_message_id=data.get("response_id"),
            sent_at=parse_datetime(data.get("datetime_entry")) if data.get("datetime_entry") else None,
        )

        # 3. Deduct inbound charge from wallet
        try:
            wallet, _ = Wallet.objects.get_or_create(account=ghl_creds)
            # Charge inbound SMS → pass sms.id as reference
            cost, segments = wallet.charge_message(
                "inbound", 
                data.get("response", ""), 
                reference_id=inbound_sms.id
            )
            inbound_sms.cost = cost
            inbound_sms.segments = segments

            # 4. Push inbound SMS into GHL conversation
            ghl_api_url = "https://services.leadconnectorhq.com/conversations/messages/inbound"

            payload = {
                "type": "SMS",
                "message": data.get("response"),
                "conversationId": conversation_id,
                "conversationProviderId": "68a3329cdef552743af9de53",
            }

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Version": "2021-04-15",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            ghl_resp = requests.post(ghl_api_url, json=payload, headers=headers)
            ghl_data = ghl_resp.json()

            if ghl_resp.status_code not in (200, 201):
                print("⚠️ Failed to push inbound SMS to GHL:", ghl_data)
                status = "failed"
            else:
                status = "delivered"
        except ValidationError as e:
            # ❌ No balance → queue inbound SMS
            inbound_sms.status = "queued"
            inbound_sms.cost = 0
            inbound_sms.segments = (len(data.get("response", "")) // 160) + 1
            ghl_data = None
            print("⚠️ Inbound SMS queued:", str(e))
        
        inbound_sms.save()

        return JsonResponse({
            "success": True,
            "ghl_response": ghl_data,
            "saved_sms_id": str(inbound_sms.id),
            "status": inbound_sms.status,
            "cost": float(inbound_sms.cost),
            "remaining_balance": float(wallet.balance),
        })

    except Exception as e:
        print("❌ Error in reply callback:", str(e))
        return JsonResponse({"error": str(e)}, status=500)

    

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

class SMSMessageListView(generics.ListAPIView):
    queryset = SMSMessage.objects.all().order_by('-created_at')  # latest first
    serializer_class = SMSMessageSerializer