from django.shortcuts import render
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import GHLTransmitSMSMapping
from core.models import GHLAuthCredentials, Wallet, WalletTransaction
from transmitsms.models import TransmitSMSAccount
from .serializers import GHLTransmitSMSMappingSerializer, SMSMessageSerializer,DashboardAnalyticsSerializer
from .tasks import update_ghl_message_status_task, urgent_update_ghl_message_status
from django.core.exceptions import ValidationError

from rest_framework import generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .filters import SMSMessageFilter  # import your filter
from rest_framework.permissions import AllowAny
from django.db.models import Sum,Avg


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
                }, status=400)
        
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
from sms_management_app.tasks import process_sms_message

@csrf_exempt
def transmit_reply_callback(request, message_id):
    """Handle incoming SMS replies from TransmitSMS (GET webhook)"""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = request.GET.dict()
        print("📩 Reply webhook received:", data)

        # Example incoming data 
        # # { 
        # # 'user_id': '197820', 
        # # 'rate': '10', 
        # # 'mobile': # '61414829252', 
        # # 'response': 'Hi there', 
        # # 'message_id': '1434767692', 
        # # 'response_id': '132669109', 
        # # 'longcode': '61430253994', 
        # # 'datetime_entry': '2025-08-14 12:33:18', 
        # # 'is_optout': 'no' 
        # # }

        # 1. Find the matching outbound SMS record
        sms_msg = get_object_or_404(SMSMessage, ghl_message_id=message_id)

        # 2. Get GHL credentials + conversation info
        ghl_creds = sms_msg.ghl_account
        conversation_id = sms_msg.ghl_conversation_id

        if not conversation_id or not ghl_creds.company_id:
            return JsonResponse({"error": "Missing GHL IDs"}, status=400)

        # 3. Create inbound SMS record (initially queued → processed by Celery)
        inbound_sms = SMSMessage.objects.create(
            ghl_account=ghl_creds,
            transmit_account=sms_msg.transmit_account,
            message_content=data.get("response"),
            to_number=data.get("longcode"),   # your business number
            from_number=data.get("mobile"),   # customer number
            direction="inbound",
            ghl_conversation_id=conversation_id,
            ghl_contact_id=sms_msg.ghl_contact_id,
            status="queued",  # Celery will update after processing
            transmit_message_id=data.get("response_id"),
            sent_at=parse_datetime(data.get("datetime_entry")) if data.get("datetime_entry") else None,
        )

        # 4. Kick off Celery task (handles wallet + GHL push with rate limits)
        process_sms_message.delay(str(inbound_sms.id))

        return JsonResponse({
            "success": True,
            "saved_sms_id": str(inbound_sms.id),
            "queued_for_processing": True,
            "status": inbound_sms.status,
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
    queryset = SMSMessage.objects.all().order_by('-created_at')
    serializer_class = SMSMessageSerializer

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = SMSMessageFilter

    # Allow searching by phone numbers or message content
    search_fields = ["to_number", "from_number", "message_content", "ghl_account__location_name", "transmit_account__account_name"]

    # Allow ordering by fields
    ordering_fields = ["created_at", "sent_at", "delivered_at", "status", "direction"]


from decimal import Decimal

@csrf_exempt
@require_http_methods(["POST"])
def wallet_add_funds(request, location_id):
    """
    Webhook endpoint to add funds to a wallet linked to a GHL account.
    URL: /wallet/<location_id>/add-funds/
    Expected JSON body: { "amount": 50.00, "reference_id": "txn_123" }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
        amount = Decimal(str(data.get("amount", 0)))
        reference_id = data.get("reference_id")

        if amount <= 0:
            return JsonResponse({"error": "Invalid amount"}, status=400)

        try:
            account = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return JsonResponse({"error": "GHL account not found"}, status=404)

        # ✅ Ensure wallet exists (create if missing)
        wallet, created = Wallet.objects.get_or_create(account=account)

        new_balance = wallet.add_funds(amount, reference_id=reference_id)

        return JsonResponse({
            "message": "Funds added successfully",
            "location_id": location_id,
            "amount_added": str(amount),
            "new_balance": str(new_balance),
            "reference_id": reference_id,
            "wallet_created": created,  # True if a new wallet was made
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    

from django.utils import timezone
from datetime import timedelta

class DashboardAnalyticsView(APIView):
    
    def get(self, request):
        # Get query params
        days = int(request.query_params.get('days', 30))
        account_id = request.query_params.get('account_id')
        
        # Date filtering
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        # Filter messages by date and optionally by account
        messages_qs = SMSMessage.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date
        )
        
        wallets_qs = Wallet.objects.all()
        accounts_qs = GHLAuthCredentials.objects.all()
        transactions_qs = WalletTransaction.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date
        )
        
        if account_id:
            messages_qs = messages_qs.filter(ghl_account__location_id=account_id)
            wallets_qs = wallets_qs.filter(account__location_id=account_id)
            accounts_qs = accounts_qs.filter(location_id=account_id)
            transactions_qs = transactions_qs.filter(wallet__account__location_id=account_id)
        
        # Calculate SMS stats
        total_messages = messages_qs.count()
        outbound_messages = messages_qs.filter(direction='outbound').count()
        inbound_messages = messages_qs.filter(direction='inbound').count()
        delivered_messages = messages_qs.filter(status='delivered').count()
        failed_messages = messages_qs.filter(status='failed').count()
        
        # Calculate delivery rate
        sent_messages = messages_qs.filter(status__in=['sent', 'delivered']).count()
        delivery_rate = (delivered_messages / sent_messages * 100) if sent_messages > 0 else 0
        
        # Calculate financial stats
        total_balance = wallets_qs.aggregate(Sum('balance'))['balance__sum'] or 0
        total_spent = transactions_qs.filter(transaction_type='debit').aggregate(Sum('amount'))['amount__sum'] or 0
        avg_cost = messages_qs.exclude(cost=0).aggregate(Avg('cost'))['cost__avg'] or 0
        
        # Account stats
        total_accounts = accounts_qs.count()
        active_mappings = GHLTransmitSMSMapping.objects.filter(ghl_account__in=accounts_qs).count()
        
        # Recent activity (24 hours)
        recent_cutoff = timezone.now() - timedelta(hours=24)
        recent_messages = messages_qs.filter(created_at__gte=recent_cutoff).count()
        recent_transactions = transactions_qs.filter(created_at__gte=recent_cutoff).count()
        
        data = {
            'total_messages': total_messages,
            'outbound_messages': outbound_messages,
            'inbound_messages': inbound_messages,
            'delivered_messages': delivered_messages,
            'failed_messages': failed_messages,
            'delivery_rate': round(delivery_rate, 1),
            'total_balance': total_balance,
            'total_spent': total_spent,
            'avg_message_cost': round(avg_cost, 4) if avg_cost else 0,
            'total_accounts': total_accounts,
            'active_mappings': active_mappings,
            'recent_messages_24h': recent_messages,
            'recent_transactions_24h': recent_transactions,
        }
        
        serializer = DashboardAnalyticsSerializer(data)
        return Response({
            'success': True,
            'data': serializer.data,
            'date_range': {
                'days': days,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d')
            }
        })