import requests
import base64
import json
from django.conf import settings
from django.utils import timezone
from .models import TransmitSMSAccount, GHLTransmitSMSMapping, SMSMessage, WebhookLog
from core.models import GHLAuthCredentials
from sms_management_app.utils import format_international

class TransmitSMSService:
    def __init__(self):
        self.base_url = "https://api.transmitsms.com"
        self.agency_api_key = settings.TRANSMIT_SMS_AGENCY_API_KEY
        self.agency_api_secret = settings.TRANSMIT_SMS_AGENCY_API_SECRET
        
    def _get_auth_header(self, api_key=None, api_secret=None):
        """Generate Basic Auth header"""
        key = api_key or self.agency_api_key
        secret = api_secret or self.agency_api_secret
        credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}

    def create_subaccount(self, name, email, phone, password):
        """Create a new subaccount in TransmitSMS"""
        url = f"{self.base_url}/add-client.json"
        headers = self._get_auth_header()
        
        data = {
            'name': name,
            'email': email,
            'msisdn': phone,
            'password': password,
        }

        print(f"[INFO] Creating subaccount for {name} ({email})")
        print(f"[DEBUG] Request URL: {url}")
        print(f"[DEBUG] Request Headers: {headers}")
        print(f"[DEBUG] Request Data: {data}")
        
        try:
            response = requests.post(url, data=data, headers=headers)
            print(f"[INFO] Response Status Code: {response.status_code}")
            print(f"[DEBUG] Raw Response: {response.text}")

            response.raise_for_status()
            result = response.json()
            print(f"[DEBUG] Parsed Response JSON: {result}")
            
            if result.get('error', {}).get('code') == 'SUCCESS':
                print(f"[SUCCESS] Subaccount created successfully for {email}")
                return {
                    'success': True,
                    'data': result
                }
            else:
                print(f"[ERROR] Failed to create subaccount: {result.get('error', {}).get('description', 'Unknown error')}")
                return {
                    'success': False,
                    'error': result.get('error', {}).get('description', 'Unknown error')
                }
                
        except requests.exceptions.RequestException as e:
            print(f"[EXCEPTION] API request failed: {str(e)}")
            return {
                'success': False,
                'error': f"API request failed: {str(e)}"
            }


    def get_existing_clients(self):
        """Get all existing clients from TransmitSMS"""
        url = f"{self.base_url}/get-clients.json"
        headers = self._get_auth_header()
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching clients: {e}")
            return None

    def find_existing_account(self, email=None, phone=None, name=None):
        """Find existing TransmitSMS account by email or phone"""
        clients_data = self.get_existing_clients()
        if not clients_data or 'clients' not in clients_data:
            return None
        
        # print("client datasssL :", clients_data)
        for client in clients_data['clients']:
            if email and client.get('email') == email:
                return client
            if phone and str(client.get('msisdn')) == str(phone):
                return client
            if name and client.get('name') == name:
                return client
        return None
    def send_sms(self, message, to_number, from_number, transmit_account, 
            dlr_callback=None, reply_callback=None, **kwargs):
        """Send SMS via TransmitSMS API with fallback if caller ID is invalid."""
        url = f"{self.base_url}/send-sms.json"
        headers = self._get_auth_header(
            transmit_account.api_key,
            transmit_account.api_secret
        )

        # Format numbers
        print("from number: before ", from_number)
        print("to number: before; ", to_number)
        to_number = format_international(to_number)
        from_number = format_international(from_number)
        print("from number: ", from_number)
        print("to number: ", to_number)

        def _make_request(payload):
            """Helper to send POST request"""
            try:
                response = requests.post(url, data=payload, headers=headers)
                print(f"➡️ Sending request with payload: {payload}")
                print(f"⬅️ Response [{response.status_code}]: {response.text}")

                response.raise_for_status()
                result = response.json()
                return {
                    'success': True,
                    'data': result,
                    'message_id': result.get('message_id')
                }
            except requests.exceptions.RequestException as e:
                return {
                    'success': False,
                    'error': str(e),
                    'response_text': getattr(e.response, "text", None)
                }

        # First attempt WITH from_number
        data = {
            'message': message,
            'to': to_number,
            'from': from_number,
        }
        if dlr_callback:
            data['dlr_callback'] = dlr_callback
        if reply_callback:
            data['reply_callback'] = reply_callback
        data.update(kwargs)

        print("🚀 First attempt with from_number")
        result = _make_request(data)

        # Retry without from_number if BAD_CALLER_ID error
        if not result['success'] and result['response_text']:
            try:
                error_json = json.loads(result['response_text'])
                if error_json.get("error", {}).get("code") == "BAD_CALLER_ID":
                    print("⚠️ BAD_CALLER_ID detected. Retrying without 'from'...")
                    data.pop('from', None)
                    result = _make_request(data)
            except Exception as parse_err:
                print("❌ Failed to parse error JSON:", parse_err)

        if result['success']:
            print("✅ SMS sent successfully:", result)
        else:
            print("❌ SMS sending failed:", result)

        return result




class GHLIntegrationService:
    def __init__(self):
        self.transmit_service = TransmitSMSService()
        
    def setup_transmit_account_for_ghl(self, ghl_account, account_details):
        """Create or link TransmitSMS account for GHL location"""
        
        # First check if account already exists
        existing_account = self.transmit_service.find_existing_account(
            email=account_details.get('email'),
            phone=account_details.get('phone'),
            name=account_details.get('name'),
        )
        
        if existing_account:
            # Link existing account
            transmit_account, created = TransmitSMSAccount.objects.get_or_create(
                account_id=str(existing_account['id']),
                defaults={
                    'account_name': existing_account['name'],
                    'email': existing_account['email'],
                    'password': account_details['password'],
                    'phone_number': str(existing_account['msisdn']),
                    'api_key': existing_account['apikey'],
                    'api_secret': existing_account['apisecret'],
                    'balance': existing_account.get('balance', 0),
                }
            )
        else:
            # Create new account
            result = self.transmit_service.create_subaccount(
                name=account_details['name'],
                email=account_details['email'],
                phone=account_details['phone'],
                password=account_details['password']
            )
            
            if not result['success']:
                raise Exception(f"Failed to create TransmitSMS account: {result['error']}")
            
            account_data = result['data']
            transmit_account = TransmitSMSAccount.objects.create(
                account_name=account_data['name'],
                email=account_data['email'],
                phone_number=str(account_data['msisdn']),
                api_key=account_data['apikey'],
                api_secret=account_data['apisecret'],
                account_id=str(account_data['id']),
                balance=account_data.get('balance', 0),
                password=account_details['password'],
                currency=account_data.get('currency', 'AUD'),
                timezone=account_data.get('timezone', 'Australia/Brisbane'),
            )
        
        # Create mapping
        mapping, created = GHLTransmitSMSMapping.objects.get_or_create(
            ghl_account=ghl_account,
            defaults={'transmit_account': transmit_account}
        )
        
        return mapping

    def process_ghl_message(self, webhook_data):
        """Process incoming message from GHL and send via TransmitSMS"""
        try:
            print("🔹 Received webhook_data:", webhook_data)

            # Extract data from GHL webhook
            location_id = webhook_data.get('locationId')
            message_content = webhook_data.get('message')
            to_number = webhook_data.get('phone')
            message_id = webhook_data.get('messageId')
            conversation_id = webhook_data.get('conversationId')
            contact_id = webhook_data.get('contactId')
            # print(f"📌 Extracted: location_id={location_id}, to_number={to_number}, message_id={message_id}")

            # Find GHL account
            ghl_account = GHLAuthCredentials.objects.get(location_id=location_id)
            # print("✅ Found GHL account:", ghl_account)

            # Find TransmitSMS mapping
            try:
                mapping = GHLTransmitSMSMapping.objects.get(ghl_account=ghl_account)
                transmit_account = mapping.transmit_account
                # print("✅ Found TransmitSMS mapping:", transmit_account)
            except GHLTransmitSMSMapping.DoesNotExist:
                print("❌ No mapping found for location:", location_id)
                raise Exception(f"No TransmitSMS account mapped for GHL location {location_id}")

            # Prepare callback URLs
            dlr_callback = f"{settings.BASE_URL}/api/sms/transmit-sms/dlr-callback/"
            reply_callback = f"{settings.BASE_URL}/api/sms/transmit-sms/reply-callback/"
            print("🔗 Callbacks prepared:", dlr_callback, reply_callback)

            # Create SMS message record
            sms_message = SMSMessage.objects.create(
                ghl_account=ghl_account,
                transmit_account=transmit_account,
                message_content=message_content,
                to_number=to_number,
                from_number=transmit_account.phone_number,
                direction='outbound',
                ghl_message_id=message_id,
                ghl_conversation_id=conversation_id,
                ghl_contact_id=contact_id,
                status='pending'
            )
            # print("📝 Created SMSMessage record:", sms_message.id)

            # Send SMS via TransmitSMS
            # print("📤 Sending SMS via TransmitSMS...")
            result = self.transmit_service.send_sms(
                message=message_content,
                to_number=to_number,
                from_number=transmit_account.phone_number,
                transmit_account=transmit_account,
                dlr_callback=dlr_callback,
                reply_callback=reply_callback
            )
            print("📩 TransmitSMS response:", result)

            if result['success']:
                sms_message.transmit_message_id = result.get('message_id')
                sms_message.status = 'sent'
                sms_message.sent_at = timezone.now()
                sms_message.save()
                # print("✅ SMS sent successfully:", sms_message.id)

                return {
                    'success': True,
                    'message_id': sms_message.id,
                    'transmit_message_id': result.get('message_id')
                }
            else:
                sms_message.status = 'failed'
                sms_message.error_message = result['error']
                sms_message.save()
                print("❌ SMS sending failed:", result['error'])

                return {
                    'success': False,
                    'error': result['error']
                }

        except Exception as e:
            print("🔥 Exception occurred:", str(e))
            return {
                'success': False,
                'error': str(e)
            }

        




def update_ghl_message_status(message_id, status, ghl_token):
    """
    Update message status in GHL conversations.
    
    Args:
        message_id (str): GHL message ID to update
        status (str): New status value
        ghl_token (str): Bearer token for GHL API
    
    Common valid statuses (based on typical messaging APIs):
    - 'delivered'
    - 'read' 
    - 'sent'
    - 'pending'
    - 'undelivered'
    
    Note: 'failed' may not be a valid status for GHL API
    """
    url = f"https://services.leadconnectorhq.com/conversations/messages/{message_id}/status"
    
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {ghl_token}",
        # "Content-Type": "application/json",
        "Version": "2021-04-15"
    }

    print(f"[DEBUG] Status being sent: '{status}'")
    if status == "failed":
        status = "pending"
    
    payload = {
        "status": status
    }
    
    try:
        print(f"[GHL API] Updating message {message_id} to status '{status}'...")
        print(f"[DEBUG] Request URL: {url}")
        print(f"[DEBUG] Request payload: {json.dumps(payload, indent=2)}")
        
        response = requests.put(url, headers=headers, data=payload)
        
        # Enhanced error handling
        if response.status_code == 422:
            print(f"[ERROR] 422 Unprocessable Entity - Invalid status value: '{status}'")
            print(f"[ERROR] Response body: {response.text}")
            print("[SUGGESTION] Try using one of these common statuses: 'delivered', 'read', 'sent', 'pending', 'undelivered'")
            return {
                "success": False, 
                "error": f"Invalid status '{status}'. Status may not be supported by GHL API.",
                "status_code": 422,
                "response_body": response.text
            }
        
        response.raise_for_status()
        print("[GHL API] Update successful:", response.json())
        return {"success": True, "data": response.json()}
    
    except requests.exceptions.RequestException as e:
        print(f"[GHL API] Update failed: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[ERROR] Status Code: {e.response.status_code}")
            print(f"[ERROR] Response Body: {e.response.text}")
        return {
            "success": False, 
            "error": str(e),
            "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
        }
