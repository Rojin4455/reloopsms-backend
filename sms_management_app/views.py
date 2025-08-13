from django.shortcuts import render
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import GHLTransmitSMSMapping
from core.models import GHLAuthCredentials
from transmitsms.models import TransmitSMSAccount
from .serializers import GHLTransmitSMSMappingSerializer


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


            
            update_ghl_message_status(
                message_id=sms_message.ghl_message_id,  # from TransmitSMS response
                status=sms_message.status,
                ghl_token=sms_message.ghl_account.access_token
            )
            
            
        except SMSMessage.DoesNotExist:
            print(f"SMS message not found for TransmitSMS ID: {message_id}")

        return JsonResponse({"message": "DLR processed"}, status=200)
    
    except Exception as e:
        print(f"DLR callback error: {e}")
        return JsonResponse({"error": str(e)}, status=500)



@csrf_exempt
def transmit_reply_callback(request):
    """Handle incoming SMS replies from TransmitSMS (GET webhook)"""
    
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        # Extract query params

        print("reply data : ", request.GET.dict())
        from_number = request.GET.get('from')
        to_number = request.GET.get('to')
        message_content = request.GET.get('message')
        
        # Log webhook for debugging
        WebhookLog.objects.create(
            webhook_type='transmit_reply',
            raw_data=request.GET.dict()
        )

        if not (from_number and to_number and message_content):
            return JsonResponse({"error": "Missing required parameters"}, status=400)

        try:
            # Find mapping
            transmit_account = TransmitSMSAccount.objects.get(phone_number=to_number)
            mapping = GHLTransmitSMSMapping.objects.get(transmit_account=transmit_account)
            ghl_account = mapping.ghl_account

            # Save in DB
            SMSMessage.objects.create(
                ghl_account=ghl_account,
                transmit_account=transmit_account,
                message_content=message_content,
                to_number=to_number,
                from_number=from_number,
                direction='inbound',
                status='delivered'
            )

            # # Forward to GHL conversation
            # ghl_token = ghl_account.api_key  # Assuming you store their GHL API key
            # conversation_url = "https://rest.gohighlevel.com/v1/conversations/messages"

            # payload = {
            #     "contactId": find_or_create_contact_in_ghl(ghl_token, from_number),
            #     "message": message_content,
            #     "type": "SMS"
            # }
            # headers = {
            #     "Authorization": f"Bearer {ghl_token}",
            #     "Content-Type": "application/json"
            # }
            # r = requests.post(conversation_url, json=payload, headers=headers)
            # r.raise_for_status()

        except (TransmitSMSAccount.DoesNotExist, GHLTransmitSMSMapping.DoesNotExist):
            return JsonResponse({"error": f"No mapping found for number {to_number}"}, status=404)

        return JsonResponse({"message": "Reply processed"}, status=200)

    except Exception as e:
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
