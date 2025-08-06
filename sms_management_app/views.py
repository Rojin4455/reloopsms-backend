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
