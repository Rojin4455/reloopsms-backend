from django.shortcuts import render
from django.http import JsonResponse
import json
import re
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import GHLTransmitSMSMapping
from core.models import GHLAuthCredentials, Wallet, WalletTransaction,TransmitNumber
from transmitsms.models import TransmitSMSAccount
from .serializers import GHLTransmitSMSMappingSerializer, SMSMessageSerializer,DashboardAnalyticsSerializer,RecentMessageSerializer, WalletTransactionSerializer, WalletSerializer, MappingSerializer,TransmitNumberSerializer
from .tasks import update_ghl_message_status_task, urgent_update_ghl_message_status
from django.core.exceptions import ValidationError

from rest_framework.generics import ListAPIView
from rest_framework import generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .filters import SMSMessageFilter  # import your filter
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Sum,Avg
from rest_framework.pagination import PageNumberPagination



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

            elif status in ["failed", "error", "hard-bounce", "soft-bounce"]:
                sms_message.status = "failed"
                sms_message.error_message = data.get("error_description", "Delivery failed")

                # 🔁 Refund wallet for failed messages
                wallet = getattr(sms_message.ghl_account, "wallet", None)
                if wallet:
                    try:
                        wallet.refund(
                            amount=wallet.outbound_segment_charge,
                            reference_id=str(sms_message.id),
                            description=f"Refund for failed message {sms_message.id}"
                        )
                    except Exception as e:
                        print(f"Refund failed for {sms_message.id}: {e}")

            elif status == "expired":
                sms_message.status = "expired"

                # 🔁 Refund wallet for expired messages
                wallet = getattr(sms_message.ghl_account, "wallet", None)
                if wallet:
                    try:
                        wallet.refund(
                            amount=wallet.outbound_segment_charge,
                            reference_id=str(sms_message.id),
                            description=f"Refund for expired message {sms_message.id}"
                        )
                    except Exception as e:
                        print(f"Refund failed for {sms_message.id}: {e}")

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
def wallet_adjust_funds(request, location_id):
    """
    Webhook endpoint to gift or take funds from a wallet.
    URL: /wallet/<location_id>/adjust-funds/
    Expected JSON body:
      {
        "action": "gift" | "take" | "add",
        "amount": 50.00,
        "reference_id": "txn_123"
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
        print("Wallet adjust funds data:", data)
        if "payment" in data:
            payment_data = data["payment"]
            reference_id = payment_data.get("transaction_id")
            amount = Decimal(str(payment_data.get("total_amount", 0)))
            action = "add"  # default or implied action for payment type
        else:
            # Standard payload handling
            action = data.get("action")
            amount = Decimal(str(data.get("amount", 0)))
            reference_id = data.get("reference_id")

            # Validate action only if no payment data
            if not action or action not in ["gift", "take", "add"]:
                return JsonResponse({"error": "Invalid or missing action"}, status=400)

        if amount <= 0:
            return JsonResponse({"error": "Invalid amount"}, status=400)

        # ✅ Get wallet
        try:
            account = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return JsonResponse({"error": "GHL account not found"}, status=404)

        wallet, _ = Wallet.objects.get_or_create(account=account)

        # ✅ Perform action
        if action == "gift":
            new_balance = wallet.add_funds(amount, reference_id=reference_id, gift=True)
            new_balance = wallet.balance
            message = "Gifted funds added successfully"

        elif action == "take":
            new_balance = wallet.deduct_funds(amount, description="Funds deducted by admin")
            message = "Funds deducted successfully"

        else:  # action == "add"
            new_balance = wallet.add_funds(amount)
            message = "Funds added successfully"

        return JsonResponse({
            "message": message,
            "action": action,
            "location_id": location_id,
            "amount": str(amount),
            "new_balance": str(new_balance),
            "reference_id": reference_id,
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _parse_sms_credit_recharge_amount(recharge_text):
    """
    Parse the credit amount from strings like '$30.00 Credit + $1 Card Fee'.
    Returns the first dollar amount (the credit), not the card fee.
    """
    if not recharge_text:
        return None
    amounts = re.findall(r"\$([\d\.]+)", str(recharge_text))
    return float(amounts[0]) if amounts else None


@csrf_exempt
@require_http_methods(["POST"])
def wallet_recharge_from_form(request):
    """
    Dedicated endpoint for SMS recharge form payload (e.g. GHL form submission).
    Does not change the existing wallet add-funds endpoint.

    Expected JSON body:
      {
        "SMS Recharge LocationID": "gKnZUcMflBkB0OAHZiZe",
        "SMS Credit Recharge": "$30.00 Credit + $1 Card Fee",
        "workflow": { "id": "...", "name": "..." }
      }
    Uses the first $ amount in "SMS Credit Recharge" as the credit (e.g. 30).
    Recharges the wallet for the given location.
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
        location_id = data.get("SMS Recharge LocationID")
        if not location_id:
            return JsonResponse(
                {"error": "SMS Recharge LocationID is required"},
                status=400,
            )
        recharge_text = data.get("SMS Credit Recharge")
        if not recharge_text:
            return JsonResponse(
                {"error": "SMS Credit Recharge is required"},
                status=400,
            )
        amount_value = _parse_sms_credit_recharge_amount(recharge_text)
        if amount_value is None or amount_value <= 0:
            return JsonResponse(
                {"error": "Could not parse a valid credit amount from SMS Credit Recharge"},
                status=400,
            )
        amount = Decimal(str(amount_value))
        workflow = data.get("workflow") or {}
        reference_id = workflow.get("id") or workflow.get("name")

        try:
            account = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return JsonResponse({"error": "GHL account not found"}, status=404)

        wallet, _ = Wallet.objects.get_or_create(account=account)
        new_balance = wallet.add_funds(amount, reference_id=reference_id)

        return JsonResponse({
            "message": "Wallet recharged successfully",
            "location_id": location_id,
            "amount": str(amount),
            "new_balance": str(new_balance),
            "reference_id": reference_id,
        })
    except json.JSONDecodeError as e:
        return JsonResponse({"error": "Invalid JSON", "detail": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


from django.utils import timezone
from datetime import timedelta

class DashboardAnalyticsView(APIView):
    
    def get(self, request):
        # Get query params
        days = int(request.query_params.get('days', 30))
        account_id = request.query_params.get('account_id')
        account_name = request.query_params.get('account_name')
        
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

        if account_name:
            messages_qs = messages_qs.filter(ghl_account__location_name__icontains=account_name)
            wallets_qs = wallets_qs.filter(account__location_name__icontains=account_name)
            accounts_qs = accounts_qs.filter(location_name__icontains=account_name)
            transactions_qs = transactions_qs.filter(wallet__account__location_name__icontains=account_name)

        
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
        latest_messages_qs = messages_qs.order_by("-created_at")[:5]
        latest_messages = RecentMessageSerializer(latest_messages_qs, many=True).data
        return Response({
            'success': True,
            'data': serializer.data,
            'recent_messages': latest_messages,
            'date_range': {
                'days': days,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': end_date.strftime('%Y-%m-%d')
            }
        })
    

class LocationMixin:
    """Utility to fetch account by locationId query param"""
    def get_account(self, request):
        location_id = request.query_params.get("locationId")
        if not location_id:
            return None, Response({"error": "locationId query parameter is required"},
                                  status=status.HTTP_400_BAD_REQUEST)
        try:
            account = GHLAuthCredentials.objects.select_related("wallet", "transmit_sms_mapping").get(
                location_id=location_id
            )
            return account, None
        except GHLAuthCredentials.DoesNotExist:
            return None, Response({"error": "Account not found"}, status=status.HTTP_404_NOT_FOUND)
    
# ---- 1. Dashboard ----
class GHLAccountDashboardAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        location_id = request.query_params.get("locationId")
        if not location_id:
            return Response({"error": "locationId query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            account = GHLAuthCredentials.objects.select_related("wallet", "transmit_sms_mapping").get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return Response({"error": "Account not found"}, status=status.HTTP_404_NOT_FOUND)

        # --- Wallet ---
        wallet_serializer = WalletSerializer(account.wallet)

        # --- Mapping ---
        mapping_serializer = MappingSerializer(account.transmit_sms_mapping) if hasattr(account, "transmit_sms_mapping") else None

        # --- Messages summary ---
        messages_qs = account.smsmessage_set.all()
        messages_summary = {
            "recent_messages": SMSMessageSerializer(messages_qs.order_by("-created_at")[:5], many=True).data,
            "total_sent": messages_qs.filter(direction="outbound").count(),
            "total_delivered": messages_qs.filter(status="delivered").count(),
            "total_failed": messages_qs.filter(status="failed").count(),
            "outbound_count": messages_qs.filter(direction="outbound").count(),
            "inbound_count": messages_qs.filter(direction="inbound").count(),
        }

        # --- Alerts ---
        alerts = {
            "low_balance": account.wallet.balance < 10,  # threshold can be customized
            "pending_messages": messages_qs.filter(status="queued").count(),
            "failed_messages": messages_qs.filter(status="failed").count()
        }

        data = {
            "account": {
                "id": account.id,
                "user_id": account.user_id,
                "company_id": account.company_id,
                "location_name": account.location_name,
                "location_id": account.location_id,
                "business_email": account.business_email,
                "business_phone": account.business_phone,
                "contact_name": account.contact_name
            },
            "wallet": wallet_serializer.data,
            "mapping": mapping_serializer.data if mapping_serializer else None,
            "messages_summary": messages_summary,
            "alerts": alerts,
        }

        return Response(data)



# ---- 2. Paginated Messages ----
class GHLAccountMessagesAPIView(LocationMixin, ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = SMSMessageSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'direction', 'to_number', 'from_number']  # filters
    ordering_fields = ['created_at', 'cost', 'segments']  # sorting
    ordering = ['-created_at']  # default

    def get_queryset(self):
        account, error = self.get_account(self.request)
        if error:
            self._error = error
            return SMSMessage.objects.none()

        qs = account.smsmessage_set.all()

        # Date range filter
        start_date = self.request.query_params.get("sent_at__gte")
        end_date = self.request.query_params.get("sent_at__lte")
        if start_date:
            qs = qs.filter(sent_at__gte=start_date)
        if end_date:
            qs = qs.filter(sent_at__lte=end_date)

        self._error = None
        return qs

    def list(self, request, *args, **kwargs):
        if hasattr(self, "_error") and self._error:
            return self._error
        return super().list(request, *args, **kwargs)


# ---- 3. Paginated Transactions ----
class GHLAccountTransactionsAPIView(LocationMixin, ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = WalletTransactionSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['transaction_type']  # basic filter
    ordering_fields = ['created_at', 'amount']
    ordering = ['-created_at']

    def get_queryset(self):
        account, error = self.get_account(self.request)
        if error:
            self._error = error
            return WalletTransaction.objects.none()

        qs = account.wallet.transactions.all()

        # Date range
        start_date = self.request.query_params.get("created_at__gte")
        end_date = self.request.query_params.get("created_at__lte")
        if start_date:
            qs = qs.filter(created_at__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__lte=end_date)

        # Amount filter
        min_amount = self.request.query_params.get("min_amount")
        max_amount = self.request.query_params.get("max_amount")
        if min_amount:
            qs = qs.filter(amount__gte=min_amount)
        if max_amount:
            qs = qs.filter(amount__lte=max_amount)

        self._error = None
        return qs

    def list(self, request, *args, **kwargs):
        if hasattr(self, "_error") and self._error:
            return self._error
        return super().list(request, *args, **kwargs)
    





class CombinedNumbersList(APIView):
    permission_classes = [AllowAny]

    class StandardResultsSetPagination(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"
        max_page_size = 50

    def paginate_queryset(self, queryset, request):
        paginator = self.StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = TransmitNumberSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def get(self, request, location_id=None):
        search_query = request.GET.get("search", "").strip()

        # Base queryset for available numbers
        available_queryset = TransmitNumber.objects.filter(status='available')

        # Apply search filter if provided
        if search_query:
            available_queryset = available_queryset.filter(number__icontains=search_query)

        response_data = {
            "available": self.paginate_queryset(available_queryset, request).data
        }

        # For registered and owned
        if location_id:
            registered_queryset = TransmitNumber.objects.filter(
                ghl_account__location_id=location_id,
                status='registered'
            )
            owned_queryset = TransmitNumber.objects.filter(
                ghl_account__location_id=location_id,
                status='owned'
            )

            # Apply same search if needed
            if search_query:
                registered_queryset = registered_queryset.filter(number__icontains=search_query)
                owned_queryset = owned_queryset.filter(number__icontains=search_query)

            response_data["registered"] = {
                "count": registered_queryset.count(),
                "results": TransmitNumberSerializer(registered_queryset, many=True).data
            }
            response_data["owned"] = {
                "count": owned_queryset.count(),
                "results": TransmitNumberSerializer(owned_queryset, many=True).data
            }
        else:
            response_data["registered"] = {"results": [], "count": 0}
            response_data["owned"] = {"results": [], "count": 0}

        return Response(response_data)


# from sms_management_app.tasks import sync_numbers
# class RegisterNumber(APIView):
#     permission_classes = [AllowAny]  # Public

#     def post(self, request):
#         number_id = request.data.get("number_id")
#         location_id = request.data.get("location_id")

#         print("hreree")

#         sync_numbers(filter_type='available')


#         if not number_id or not location_id:
#             return Response({"error": "number_id and location_id are required"}, status=status.HTTP_400_BAD_REQUEST)

#         try:
#             number_obj = TransmitNumber.objects.get(id=number_id, status="available")
#         except TransmitNumber.DoesNotExist:
#             return Response({"error": "Number not available"}, status=status.HTTP_404_NOT_FOUND)
#         ghl = GHLAuthCredentials.objects.get(location_id=location_id)
#         number_obj.status = "registered"
#         number_obj.ghl_account = ghl
#         number_obj.save()

#         return Response({"message": "Number registered successfully"}, status=status.HTTP_200_OK)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50


# 1️⃣ Get all available numbers (Public endpoint)
# class GetAvailableNumbers(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):

#         search_query = request.GET.get("search", "").strip()

#         queryset = TransmitNumber.objects.filter(status='available')
#         if search_query:
#             queryset = queryset.filter(number__icontains=search_query)

#         paginator = StandardResultsSetPagination()
#         page = paginator.paginate_queryset(queryset, request)
#         serializer = TransmitNumberSerializer(page, many=True)
#         return paginator.get_paginated_response(serializer.data)


# 2️⃣ Get registered and owned numbers for a location (Authenticated endpoint)
class GetLocationNumbers(APIView):
    permission_classes = [AllowAny]

    def get(self, request, location_id):
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)


        search_query = request.GET.get("search", "").strip()

        registered_queryset = TransmitNumber.objects.filter(
            ghl_account__location_id=location_id,
            status='pending'
        )
        owned_queryset = TransmitNumber.objects.filter(
            ghl_account__location_id=location_id,
            status='owned'
        )

        # Apply search
        if search_query:
            registered_queryset = registered_queryset.filter(number__icontains=search_query)
            owned_queryset = owned_queryset.filter(number__icontains=search_query)

        response_data = {
            "pending": {
                "count": registered_queryset.count(),
                "results": TransmitNumberSerializer(registered_queryset, many=True).data
            },
            "owned": {
                "count": owned_queryset.count(),
                "results": TransmitNumberSerializer(owned_queryset, many=True).data
            },
        }

        return Response(response_data, status=status.HTTP_200_OK)
    

    def patch(self, request, location_id):
        """
        Toggle is_active for a specific TransmitNumber.
        Expects JSON payload: {"number": "<number>", "is_active": false}
        """
        number_value = request.data.get("number")
        is_active = request.data.get("is_active")

        if number_value is None or is_active is None:
            return Response(
                {"error": "Both 'number' and 'is_active' are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            transmit_number = TransmitNumber.objects.get(
                ghl_account__location_id=location_id,
                number=number_value
            )
        except TransmitNumber.DoesNotExist:
            return Response(
                {"error": f"Transmit number {number_value} not found for this location"},
                status=status.HTTP_404_NOT_FOUND
            )

        transmit_number.is_active = bool(is_active)
        transmit_number.save(update_fields=["is_active"])

        return Response(
            {
                "message": f"Transmit number {number_value} updated successfully",
                "number": transmit_number.number,
                "is_active": transmit_number.is_active
            },
            status=status.HTTP_200_OK
        )
    
class OwnNumber(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        number_id = request.data.get("number_id")
        location_id = request.data.get("location_id")

        # Optionally sync latest numbers (can skip if not needed every time)
        sync_numbers(filter_type='available')

        # Validate input
        if not number_id or not location_id:
            return Response(
                {"error": "number_id and location_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if number exists (can be available, registered, etc.)
        try:
            number_obj = TransmitNumber.objects.get(id=number_id)
        except TransmitNumber.DoesNotExist:
            return Response(
                {"error": "Number not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Fetch the GHL account
        try:
            ghl = GHLAuthCredentials.objects.get(location_id=location_id)
        except GHLAuthCredentials.DoesNotExist:
            return Response(
                {"error": "Invalid location_id"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update number ownership
        number_obj.status = "owned"
        number_obj.ghl_account = ghl
        number_obj.save()

        return Response(
            {"message": f"Number {number_obj.number} marked as owned successfully"},
            status=status.HTTP_200_OK
        )

# class OwnNumber(APIView):
#     permission_classes = [IsAuthenticated]  # Make public or adjust permissions

#     def post(self, request):
#         number_id = request.data.get("number_id")
#         location_id = request.data.get("location_id")
#         forward_url = request.data.get("forward_url")  # Optional callback

#         if not number_id or not location_id:
#             return Response(
#                 {"error": "number_id and location_id are required"},
#                 status=status.HTTP_400_BAD_REQUEST
#             )

#         try:
#             number_obj = TransmitNumber.objects.get(id=number_id, status="available")
#         except TransmitNumber.DoesNotExist:
#             return Response({"error": "Number not available"}, status=status.HTTP_404_NOT_FOUND)

#         try:
#             ghl = GHLAuthCredentials.objects.get(location_id=location_id)
#             transmit_account = ghl.transmit_sms_mapping.transmit_account
#         except GHLAuthCredentials.DoesNotExist:
#             return Response({"error": "GHL account not found for this location"}, status=status.HTTP_404_NOT_FOUND)
#         except GHLTransmitSMSMapping.DoesNotExist:
#             return Response({"error": "Mapping not found for this GHL account"}, status=status.HTTP_404_NOT_FOUND)
#         except TransmitSMSAccount.DoesNotExist:
#             return Response({"error": "Transmit account not found"}, status=status.HTTP_404_NOT_FOUND)

#         # Initialize TransmitSMS service
#         transmit_service = TransmitSMSService()

#         # Prepare payload for owning the number
#         payload = {}
#         if number_obj.number:
#             payload['number'] = number_obj.number
#         if forward_url:
#             payload['forward_url'] = forward_url

#         url = f"{transmit_service.base_url}/lease-number.json"
#         headers = transmit_service._get_auth_header(api_key=transmit_account.api_key, api_secret=transmit_account.api_secret)  # Use agency credentials

#         try:
#             response = requests.post(url, data=payload, headers=headers)
#             response.raise_for_status()
#             result = response.json()
#         except requests.exceptions.RequestException as e:
#             return Response({"error": "Failed to call TransmitSMS API", "details": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#         # Check API response success
#         if result.get("error", {}).get("code") == "SUCCESS":
#             number_obj.status = "owned"
#             number_obj.ghl_account = ghl
#             number_obj.save()
#             return Response({
#                 "message": "Number successfully owned",
#                 "data": result
#             }, status=status.HTTP_200_OK)
#         else:
#             return Response({
#                 "error": "Failed to own the number",
#                 "details": result.get("error")
#             }, status=status.HTTP_400_BAD_REQUEST)







# ============================================
# 1. Get Available Numbers (Real-time fetch)
# ============================================
class GetAvailableNumbers(APIView):
    """
    Fetch available numbers directly from Transmit SMS API in real-time.
    Supports search, pagination, and filtering by price/label.
    """
    permission_classes = [AllowAny]

    def get(self, request):


        print("heyyyy")
        # Extract query parameters
        search_query = request.GET.get("search", "").strip()
        price_min = request.GET.get("price_min")
        price_max = request.GET.get("price_max")
        label_filter = request.GET.get("label")  # 'standard' or 'premium'
        sort_by = request.GET.get("sort_by", "price_asc")  # price_asc, price_desc
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))

        try:
            # Fetch numbers directly from Transmit SMS
            service = TransmitSMSService()
            transmit_response = service.get_dedicated_numbers(filter_type='available')
            
            numbers_data = transmit_response.get("data", {}).get("numbers", [])
            
            # Transform to our format
            available_numbers = []
            for num in numbers_data:
                number_str = str(num["number"])
                price = Decimal(str(num.get("price", 0)))
                
                # Determine label (Standard if price <= 11, Premium if > 11)
                label = "Standard" if price <= 11 else "Premium"
                
                available_numbers.append({
                    "number": number_str,
                    "price": float(price),
                    "label": label,
                    "display_price": f"${price}",
                    "can_auto_register": price <= 11  # Only standard can auto-register
                })
            
            # Apply filters
            filtered_numbers = self._apply_filters(
                available_numbers, 
                search_query, 
                price_min, 
                price_max, 
                label_filter
            )
            
            # Apply sorting
            filtered_numbers = self._apply_sorting(filtered_numbers, sort_by)
            
            # Manual pagination
            total_count = len(filtered_numbers)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_numbers = filtered_numbers[start_idx:end_idx]
            
            # Build response
            response_data = {
                "count": total_count,
                "next": page * page_size < total_count,
                "previous": page > 1,
                "page": page,
                "page_size": page_size,
                "total_pages": (total_count + page_size - 1) // page_size,
                "results": paginated_numbers
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {"error": f"Failed to fetch numbers: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _apply_filters(self, numbers, search, price_min, price_max, label):
        """Apply search and filter criteria"""
        filtered = numbers
        
        # Search by digits
        if search:
            filtered = [n for n in filtered if search in n["number"]]
        
        # Price range filter
        if price_min:
            filtered = [n for n in filtered if n["price"] >= float(price_min)]
        if price_max:
            filtered = [n for n in filtered if n["price"] <= float(price_max)]
        
        # Label filter (standard/premium)
        if label:
            label_lower = label.lower()
            filtered = [n for n in filtered if n["label"].lower() == label_lower]
        
        return filtered
    
    def _apply_sorting(self, numbers, sort_by):
        """Apply sorting"""
        if sort_by == "price_desc":
            return sorted(numbers, key=lambda x: x["price"], reverse=True)
        else:  # default price_asc
            return sorted(numbers, key=lambda x: x["price"])


from django.db import transaction


# ============================================
# 3. Register Number (Standard - Auto)
# ============================================
class RegisterNumber(APIView):

    

    """
    Register a Standard number (price <= $11).
    Auto-purchases via Transmit SMS API if quota available.
    If quota exceeded, attempts wallet purchase.
    """
    
    permission_classes = [AllowAny]

    def post(self, request):
        number = request.data.get("number")  # The actual phone number string
        location_id = request.data.get("location_id")

        if not number or not location_id:
            return Response(
                {"error": "number and location_id are required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Get GHL account
            ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)
            wallet = getattr(ghl_account, "wallet", None)

            # ✅ Fetch number details from Transmit API
            service = TransmitSMSService()
            transmit_response = service.get_dedicated_numbers(filter_type='available')
            numbers_data = transmit_response.get("data", {}).get("numbers", [])
            
            number_info = next((n for n in numbers_data if str(n["number"]) == number), None)
            # if number_info:
            #     return Response(
            #         {"error": "Number not found or no longer available"},
            #         status=status.HTTP_404_NOT_FOUND
            #     )
            price=11

            # price = Decimal(str(number_info.get("price", 0)))

            # ✅ Only allow standard numbers here
            if price > 11:
                return Response(
                    {"error": "This is a Premium number. Please use the Request flow."}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Check if already owned
            if TransmitNumber.objects.filter(number=number, ghl_account=ghl_account, status='owned').exists():
                return Response(
                    {"error": "This number is already owned."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Check subscription entitlement first
            use_wallet = False
            charge_amount = Decimal('0.00')

            if ghl_account.can_purchase_standard():
                # Subscription slot available
                ghl_account.current_standard_purchased += 1
                ghl_account.save(update_fields=["current_standard_purchased"])

            else:
                # No subscription quota — attempt wallet charge
                use_wallet = True
                charge_amount = price

                if not wallet:
                    return Response(
                        {"error": "Wallet not found for this account."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if not ghl_account.has_sufficient_wallet_balance(price):
                    return Response(
                        {"error": "Insufficient wallet balance to purchase this number."},
                        status=status.HTTP_402_PAYMENT_REQUIRED
                    )

                # Deduct funds from wallet
                wallet.deduct_funds(price, description=f"Purchase of extra standard number {number}")

            # ✅ Register (and simulate purchase) on Transmit
            purchase_response = service.purchase_number(number)

            transmit_account = ghl_account.transmit_sms_mapping.transmit_account
            if not purchase_response.get("success", False):
                # Refund if wallet was used
                if use_wallet:
                    wallet.refund(price, description=f"Refund for failed purchase of {number}")
                return Response(
                    {"error": f"Failed to purchase number from Transmit: {purchase_response.get('error')}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # ✅ Extract renewal date safely
            data = purchase_response.get("data", {})
            next_charge_str = data.get("next_charge")
            next_renewal_date = None
            if next_charge_str:
                try:
                    from datetime import datetime
                    next_renewal_date = datetime.strptime(next_charge_str, "%Y-%m-%d").date()
                except ValueError:
                    print(f"⚠️ Invalid next_charge date format: {next_charge_str}")

            # ✅ Save number to DB
            with transaction.atomic():
                TransmitNumber.objects.create(
                    ghl_account=ghl_account,
                    number=number,
                    price=price,
                    status="owned",
                    purchased_at=timezone.now(),
                    registered_at=timezone.now(),
                    is_extra_number=use_wallet,
                    monthly_charge=price if use_wallet else Decimal('0.00'),
                    last_billed_at=timezone.now() if use_wallet else None,
                    next_renewal_date=next_renewal_date  # ✅ added here
                )
                trigger_inbound_webhook(ghl_account.location_name, ghl_account.location_id, number, str(price), "owned",'standard')

            return Response({
                "message": "Number registered and purchased successfully",
                "number": number,
                "price": float(price),
                "charged": float(charge_amount) if use_wallet else 0.0,
                "payment_method": "wallet" if use_wallet else "subscription",
                "is_extra": use_wallet,
                "next_renewal_date": str(next_renewal_date) if next_renewal_date else None,  # ✅ optional: return in response
            }, status=status.HTTP_200_OK)

        except GHLAuthCredentials.DoesNotExist:
            return Response(
                {"error": "Invalid location_id"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

def trigger_inbound_webhook(location_name, location_id, number, price, status, number_type):
    """
    Triggers an inbound webhook after number purchase.
    """
    webhook_url = (
        "https://services.leadconnectorhq.com/hooks/fM52tHdamVZya3QZH3ck/webhook-trigger/"
        "f5e74905-7df1-46e7-9d2d-44da2a81add0"
    )

    params = {
        "email": "test@test.com",
        "location_name": location_name,
        "location_id": location_id,
        "number": number,
        "price": price,
        "status": status,
        "number_type": number_type,  # 'standard' or 'premium'
    }

    try:
        response = requests.get(webhook_url, params=params, timeout=10)
        response.raise_for_status()
        print("✅ Webhook triggered successfully:", response.status_code)
    except requests.RequestException as e:
        print("❌ Webhook trigger failed:", str(e))  

# trigger_inbound_webhook("test_location_name","testlocationid","number", "11", "owned", "standard")

 

class RequestPremiumNumber(APIView):
    """
    Request a Premium number (price > $11).
    This does NOT purchase immediately — it saves a request for admin review.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        number = request.data.get("number")
        location_id = request.data.get("location_id")

        if not number or not location_id:
            return Response(
                {"error": "number and location_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)

            # ✅ Fetch number details from Transmit API
            service = TransmitSMSService()
            transmit_response = service.get_dedicated_numbers(filter_type='available')
            numbers_data = transmit_response.get("data", {}).get("numbers", [])

            number_info = next((n for n in numbers_data if str(n["number"]) == number), None)
            if not number_info:
                return Response(
                    {"error": "Number not found or no longer available"},
                    status=status.HTTP_404_NOT_FOUND
                )

            price = Decimal(str(number_info.get("price", 0)))

            # ✅ Must be a premium number
            if price <= 11:
                return Response(
                    {"error": "This is a Standard number. Please use the standard registration flow."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Check if already owned or requested
            if TransmitNumber.objects.filter(number=number, ghl_account=ghl_account, status="owned").exists():
                return Response(
                    {"error": "This number is already owned."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if TransmitNumber.objects.filter(
                number=number, ghl_account=ghl_account, status="pending"
            ).exists():
                return Response(
                    {"error": "You already have a pending request for this number."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Create request record
            TransmitNumber.objects.create(
                ghl_account=ghl_account,
                number=number,
                price=price,
                status="pending"
            )

            return Response({
                "message": "Your premium number request has been submitted successfully.",
                "number": number,
                "price": float(price),
                "status": "pending"
            }, status=status.HTTP_200_OK)

        except GHLAuthCredentials.DoesNotExist:
            return Response(
                {"error": "Invalid location_id"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RegisterPremiumNumber(APIView):
    """
    Register a Premium number (price > $11).
    Uses subscription quota if available, else charges from wallet.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        number = request.data.get("number")
        location_id = request.data.get("location_id")

        if not number or not location_id:
            return Response(
                {"error": "number and location_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)
            wallet = getattr(ghl_account, "wallet", None)

            # ✅ Fetch number details from Transmit API
            service = TransmitSMSService()
            transmit_response = service.get_dedicated_numbers(filter_type='available')
            numbers_data = transmit_response.get("data", {}).get("numbers", [])

            number_info = next((n for n in numbers_data if str(n["number"]) == number), None)
            if not number_info:
                return Response(
                    {"error": "Number not found or no longer available"},
                    status=status.HTTP_404_NOT_FOUND
                )

            price = Decimal(str(number_info.get("price", 0)))

            # ✅ Must be premium (> $11)
            if price <= 11:
                return Response(
                    {"error": "This is a Standard number. Please use the standard registration flow."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ✅ Check if already owned
            if TransmitNumber.objects.filter(number=number, ghl_account=ghl_account, status='owned').exists():
                return Response(
                    {"error": "This number is already owned."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            

            # ✅ Determine how to pay: subscription or wallet
            use_wallet = False
            charge_amount = Decimal('0.00')

            if ghl_account.can_purchase_premium():
                # Within subscription quota
                ghl_account.current_premium_purchased += 1
                ghl_account.save(update_fields=["current_premium_purchased"])
            else:
                # No premium quota, charge from wallet
                use_wallet = True
                charge_amount = price

                if not wallet:
                    return Response(
                        {"error": "Wallet not found for this account."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if not ghl_account.has_sufficient_wallet_balance(price):
                    return Response(
                        {"error": "Insufficient wallet balance to purchase this premium number."},
                        status=status.HTTP_402_PAYMENT_REQUIRED
                    )

                # Deduct funds
                wallet.deduct_funds(price, description=f"Purchase of premium number {number}")

            # ✅ Purchase number via Transmit API
            purchase_response = service.purchase_number(number)
            if not purchase_response.get("success", False):
                # Refund if wallet used
                if use_wallet:
                    wallet.refund(price, description=f"Refund for failed purchase of {number}")
                return Response(
                    {"error": f"Failed to purchase number from Transmit: {purchase_response.get('error')}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # ✅ Extract renewal date from Transmit response
            data = purchase_response.get("data", {})
            next_charge_str = data.get("next_charge")
            next_renewal_date = None
            if next_charge_str:
                from datetime import datetime
                try:
                    next_renewal_date = datetime.strptime(next_charge_str, "%Y-%m-%d").date()
                except ValueError:
                    print(f"⚠️ Invalid next_charge date format: {next_charge_str}")

            # ✅ Save number in DB
            with transaction.atomic():
                TransmitNumber.objects.update_or_create(
                    ghl_account=ghl_account,
                    number=number,
                    defaults={
                        "price": price,
                        "status": "owned",
                        "purchased_at": timezone.now(),
                        "registered_at": timezone.now(),
                        "is_extra_number": use_wallet,
                        "monthly_charge": price if use_wallet else Decimal("0.00"),
                        "last_billed_at": timezone.now() if use_wallet else None,
                        "next_renewal_date": next_renewal_date,  # ✅ Added
                    },
                )

                trigger_inbound_webhook(ghl_account.location_name, ghl_account.location_id, number, str(price), "owned",'premium')

            # ✅ Response payload
            return Response({
                "message": "Premium number registered successfully",
                "number": number,
                "price": float(price),
                "charged": float(charge_amount) if use_wallet else 0.0,
                "payment_method": "wallet" if use_wallet else "subscription",
                "is_extra": use_wallet,
                "next_renewal_date": str(next_renewal_date) if next_renewal_date else None,  # ✅ Optional: include in response
            }, status=status.HTTP_200_OK)


        except GHLAuthCredentials.DoesNotExist:
            return Response(
                {"error": "Invalid location_id"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        



class RemoveNumberFromLocation(APIView):
    """
    Remove (unlink) a number from a given location.
    - Resets status to 'available'
    - Clears ghl_account mapping
    - Optionally deletes the record entirely if you prefer
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, location_id):
        number_value = request.data.get("number")

        if not number_value:
            return Response(
                {"error": "number is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            transmit_number = TransmitNumber.objects.get(
                ghl_account__location_id=location_id,
                number=number_value
            )
        except TransmitNumber.DoesNotExist:
            return Response(
                {"error": f"Number {number_value} not found for this location."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Option 1️⃣: Keep in DB but mark available
        transmit_number.status = "available"
        transmit_number.ghl_account = None
        transmit_number.is_active = False
        transmit_number.save(update_fields=["status", "ghl_account", "is_active"])

        # Option 2️⃣ (Alternative): Uncomment to delete record entirely
        # transmit_number.delete()

        return Response(
            {
                "message": f"Number {number_value} removed successfully from location {location_id}.",
                "number": number_value,
                "status": "available"
            },
            status=status.HTTP_200_OK
        )
    


class SendQueuedMessagesAPIView(APIView):
    """
    API endpoint to manually send queued messages by providing a list of message IDs.
    Accepts POST request with:
    {
        "message_ids": ["uuid1", "uuid2", ...],
        "location_id": "optional_location_id"  # Optional: filter by location
    }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        message_ids = request.data.get("message_ids", [])
        location_id = request.data.get("location_id")

        if not message_ids:
            return Response(
                {"error": "message_ids is required and must be a non-empty list"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(message_ids, list):
            return Response(
                {"error": "message_ids must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get messages
        try:
            messages = SMSMessage.objects.filter(
                id__in=message_ids,
                status="queued"
            )
            
            if location_id:
                messages = messages.filter(ghl_account__location_id=location_id)
            
            if not messages.exists():
                return Response(
                    {"error": "No queued messages found with the provided IDs"},
                    status=status.HTTP_404_NOT_FOUND
                )

            results = {
                "successful": [],
                "failed": [],
                "skipped": []
            }

            from sms_management_app.tasks import process_sms_message
            from sms_management_app.services import GHLIntegrationService
            from django.utils import timezone

            for sms in messages:
                try:
                    wallet = Wallet.objects.get(account=sms.ghl_account)
                    
                    if sms.direction == "outbound":
                        # Handle outbound messages
                        try:
                            cost, segments = wallet.charge_message(
                                "outbound", 
                                sms.message_content, 
                                reference_id=sms.id
                            )
                            sms.cost = cost
                            sms.segments = segments
                            
                            service = GHLIntegrationService()
                            result = service.send_outbound_sms(sms, cost, segments)
                            
                            if result.get("success"):
                                sms.status = "sent"
                                sms.sent_at = timezone.now()
                                sms.transmit_message_id = result.get("transmit_message_id")
                                sms.save()
                                results["successful"].append({
                                    "message_id": str(sms.id),
                                    "direction": "outbound",
                                    "status": "sent"
                                })
                            else:
                                # Refund if failed
                                wallet.refund(
                                    cost, 
                                    reference_id=sms.id, 
                                    description="Refund for failed queued SMS"
                                )
                                sms.status = "failed"
                                sms.error_message = result.get("error", "Unknown error")
                                sms.save()
                                results["failed"].append({
                                    "message_id": str(sms.id),
                                    "direction": "outbound",
                                    "error": result.get("error", "Unknown error")
                                })
                        except ValidationError as e:
                            # Insufficient funds
                            sms.status = "queued"
                            sms.save()
                            results["failed"].append({
                                "message_id": str(sms.id),
                                "direction": "outbound",
                                "error": f"Insufficient balance: {str(e)}"
                            })
                        except Exception as e:
                            sms.status = "failed"
                            sms.error_message = str(e)
                            sms.save()
                            results["failed"].append({
                                "message_id": str(sms.id),
                                "direction": "outbound",
                                "error": str(e)
                            })

                    elif sms.direction == "inbound":
                        # Handle inbound messages - enqueue Celery task
                        process_sms_message.delay(str(sms.id))
                        results["successful"].append({
                            "message_id": str(sms.id),
                            "direction": "inbound",
                            "status": "queued_for_processing"
                        })
                    else:
                        results["skipped"].append({
                            "message_id": str(sms.id),
                            "reason": f"Unknown direction: {sms.direction}"
                        })

                except Wallet.DoesNotExist:
                    results["failed"].append({
                        "message_id": str(sms.id),
                        "error": "Wallet not found for this account"
                    })
                except Exception as e:
                    results["failed"].append({
                        "message_id": str(sms.id),
                        "error": str(e)
                    })

            return Response({
                "message": f"Processed {len(messages)} messages",
                "results": results,
                "summary": {
                    "total": len(messages),
                    "successful": len(results["successful"]),
                    "failed": len(results["failed"]),
                    "skipped": len(results["skipped"])
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Error processing messages: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def test_own_number(number, price, ghl_account):
    TransmitNumber.objects.create(
        ghl_account=ghl_account,
        number=number,
        price=price,
        status="owned",
        purchased_at=timezone.now(),
        registered_at=timezone.now(),
        # is_extra_number=use_wallet,
        # monthly_charge=price if use_wallet else Decimal('0.00'),
        # last_billed_at=timezone.now() if use_wallet else None,
        # next_renewal_date=next_renewal_date  # ✅ added here
    )


