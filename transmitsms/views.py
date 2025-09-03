from django.shortcuts import render
from rest_framework import viewsets

from .models import TransmitSMSAccount
from .serializers import TransmitSMSAccountSerializer
from django.views.decorators.csrf import csrf_exempt
# Create your views here.

class TransmitSMSAccountViewSet(viewsets.ModelViewSet):
    queryset = TransmitSMSAccount.objects.all()
    serializer_class = TransmitSMSAccountSerializer




from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt



@csrf_exempt
def drl_callback_view(request):
    if request.method != "POST":
        data = json.loads(request.body)
        print("date:----- ", data)

        return JsonResponse({"message":"Webhook received"}, status=200)
        

    try:
        data = json.loads(request.body)
        print("date:----- ", data)
        # WebhookLog.objects.create(data=data)
        # event_type = data.get("type")
        # handle_webhook_event.delay(data, event_type)
        return JsonResponse({"message": "Method not allowed"}, status=405)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
