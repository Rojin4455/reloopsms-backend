import requests
from django.conf import settings

from core.ghl_auth import ghl_request

GHL_BASE_URL = "https://services.leadconnectorhq.com/objects/custom_objects.sms_credits"
GHL_API_VERSION = "2021-07-28"

class GHLService:
    def __init__(self, access_token, auth_credentials=None):
        self.access_token = access_token
        self.auth_credentials = auth_credentials

    def headers(self):
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def _request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", self.headers())
        response = ghl_request(
            method,
            url,
            headers=headers,
            auth_credentials=self.auth_credentials,
            timeout=kwargs.pop("timeout", 30),
            **kwargs,
        )
        if self.auth_credentials is not None:
            self.access_token = self.auth_credentials.access_token
        return response

    def search_records(self, main_location_id, page=1, page_limit=50):
        payload = {"locationId": main_location_id, "page": page, "pageLimit": page_limit}
        r = self._request("POST", f"{GHL_BASE_URL}/records/search", json=payload)
        r.raise_for_status()
        return r.json()

    def create_record(self, main_location_id, properties):
        payload = {"locationId": main_location_id, "properties": properties}
        r = self._request("POST", f"{GHL_BASE_URL}/records", json=payload)
        r.raise_for_status()
        return r.json()

    def get_record(self, record_id, main_location_id):
        # If GHL provides a GET endpoint, prefer it. If not, fallback to search and filter by id.
        r = self._request(
            "POST",
            f"{GHL_BASE_URL}/records/search",
            json={"locationId": main_location_id, "page": 1, "pageLimit": 1, "searchAfter": None},
        )
        r.raise_for_status()
        # fallback simple scan — you may adapt if API has GET /records/{id}
        data = r.json()
        print("data: ",data)
        for rec in data.get("records", []):
            if rec.get("id") == record_id:
                return rec
        return None

    def get_record_by_id(self, record_id):
        """
        Get a record directly by ID using GET endpoint.
        
        Args:
            record_id: The record ID to retrieve
            
        Returns:
            dict: Record data if found, None otherwise
        """
        import json
        url = f"{GHL_BASE_URL}/records/{record_id}"
        print(f"📋 [get_record_by_id] URL: {url}")
        print(f"📋 [get_record_by_id] Record ID: {record_id}")
        try:
            r = self._request("GET", url)
            print(f"📋 [get_record_by_id] Response Status: {r.status_code}")
            r.raise_for_status()
            response_data = r.json()
            print(f"📋 [get_record_by_id] Response: {json.dumps(response_data, indent=2)}")
            # Extract the record from the response
            record = response_data.get("record")
            print(f"📋 [get_record_by_id] Record extracted: {record is not None}")
            return record
        except requests.exceptions.HTTPError as e:
            print(f"📋 [get_record_by_id] HTTP Error: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                print(f"📋 [get_record_by_id] Record not found (404)")
                return None
            raise
        except Exception as e:
            print(f"📋 [get_record_by_id] Exception: {e}")
            import traceback
            print(f"📋 [get_record_by_id] Traceback: {traceback.format_exc()}")
            return None


    def update_record(self, record_id, main_location_id, payload):
        import json
        url = f"{GHL_BASE_URL}/records/{record_id}?locationId={main_location_id}"
        payload = {
            "properties": payload
        }

        print("\n===== GHL Update Record Debug =====")
        print(f"➡️ URL: {url}")
        # print(f"➡️ Headers: {json.dumps(self.headers(), indent=2)}")
        print(f"➡️ Payload: {json.dumps(payload, indent=2)}")

        r = self._request("PUT", url, json=payload)

        print(f"⬅️ Response Status: {r.status_code}")
        try:
            print(f"⬅️ Response JSON: {json.dumps(r.json(), indent=2)}")
        except Exception:
            print(f"⬅️ Response Text: {r.text}")

        r.raise_for_status()
        return r.json()

    def _normalize_props(self, props: dict) -> dict:
        """
        Normalize properties for GHL API payload.
        - Wrap int/float/Decimal into {currency, value}
        - Leave strings/emails/etc as-is
        """

        from decimal import Decimal

        normalized = {}
        for key, value in props.items():
            if isinstance(value, Decimal):
                value = float(value)  # Convert safely
            if isinstance(value, (int, float)):
                normalized[key] = {"currency": "default", "value": value}
            else:
                normalized[key] = value
        return normalized

    def get_contact(self, contact_id):
        """
        Get a contact by ID from GHL.
        
        Args:
            contact_id: The contact ID to retrieve
            
        Returns:
            dict: Contact data if found, None otherwise
        """
        url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"
        print(f"🔍 [get_contact] URL: {url}")
        print(f"🔍 [get_contact] Contact ID: {contact_id}")
        try:
            r = self._request("GET", url)
            print(f"🔍 [get_contact] Response Status: {r.status_code}")
            r.raise_for_status()
            contact_data = r.json()
            print(f"🔍 [get_contact] Contact found: {contact_data}")
            return contact_data
        except requests.exceptions.HTTPError as e:
            print(f"🔍 [get_contact] HTTP Error: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                print(f"🔍 [get_contact] Contact not found (404)")
                return None
            raise
        except Exception as e:
            print(f"🔍 [get_contact] Exception: {e}")
            import traceback
            print(f"🔍 [get_contact] Traceback: {traceback.format_exc()}")
            return None

    def update_contact_custom_field(self, contact_id, custom_field_id, field_value):
        """
        Update a contact's custom field in GHL.
        
        Args:
            contact_id: The contact ID to update
            custom_field_id: The custom field ID to update
            field_value: The value to set for the custom field
            
        Returns:
            dict: Updated contact data if successful
        """
        import json
        url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"
        payload = {
            "customFields": [
                {
                    "id": custom_field_id,
                    "field_value": str(field_value)
                }
            ]
        }
        print(f"✏️ [update_contact_custom_field] URL: {url}")
        print(f"✏️ [update_contact_custom_field] Payload: {json.dumps(payload, indent=2)}")
        print(f"✏️ [update_contact_custom_field] Headers: {json.dumps({k: v for k, v in self.headers().items() if k != 'Authorization'}, indent=2)}")
        
        try:
            r = self._request("PUT", url, json=payload)
            print(f"✏️ [update_contact_custom_field] Response Status: {r.status_code}")
            print(f"✏️ [update_contact_custom_field] Response: {r.text}")
            r.raise_for_status()
            result = r.json()
            print(f"✏️ [update_contact_custom_field] Success! Result: {json.dumps(result, indent=2)}")
            return result
        except Exception as e:
            print(f"✏️ [update_contact_custom_field] Exception: {e}")
            import traceback
            print(f"✏️ [update_contact_custom_field] Traceback: {traceback.format_exc()}")
            raise