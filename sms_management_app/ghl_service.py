import requests
from django.conf import settings
from core.models import GHLAuthCredentials

class GHLAPIService:
    def __init__(self, ghl_account):
        self.ghl_account = ghl_account
        self.base_url = "https://services.leadconnectorhq.com"
        self.headers = {
            'Authorization': f'Bearer {ghl_account.access_token}',
            'Version': '2021-07-28',
            'Content-Type': 'application/json'
        }

    def refresh_token_if_needed(self):
        """Refresh access token if expired"""
        # Implementation depends on your token refresh logic
        pass

    def get_location_details(self):
        """Get detailed location information"""
        try:
            response = requests.get(
                f"{self.base_url}/locations/{self.ghl_account.location_id}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching location details: {e}")
            return None

    def send_message_to_conversation(self, conversation_id, message, message_type="SMS"):
        """Send a message to a GHL conversation (for replies)"""
        try:
            data = {
                "type": message_type,
                "message": message,
                "html": message
            }
            
            response = requests.post(
                f"{self.base_url}/conversations/{conversation_id}/messages",
                headers=self.headers,
                json=data
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Error sending message to GHL conversation: {e}")
            return None

    def update_contact(self, contact_id, updates):
        """Update contact information in GHL"""
        try:
            response = requests.put(
                f"{self.base_url}/contacts/{contact_id}",
                headers=self.headers,
                json=updates
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Error updating GHL contact: {e}")
            return None

    def get_conversation_details(self, conversation_id):
        """Get conversation details from GHL"""
        try:
            response = requests.get(
                f"{self.base_url}/conversations/{conversation_id}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching conversation details: {e}")
            return None
