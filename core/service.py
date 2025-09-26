import requests
from django.conf import settings

GHL_BASE_URL = "https://services.leadconnectorhq.com/objects/custom_objects.sms_credits"
GHL_API_VERSION = "2021-07-28"

class GHLService:
    def __init__(self, access_token):
        self.access_token = access_token

    def headers(self):
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def search_records(self, main_location_id, page=1, page_limit=50):
        payload = {"locationId": main_location_id, "page": page, "pageLimit": page_limit}
        r = requests.post(f"{GHL_BASE_URL}/records/search", json=payload, headers=self.headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def create_record(self, main_location_id, properties):
        payload = {"locationId": main_location_id, "properties": properties}
        r = requests.post(f"{GHL_BASE_URL}/records", json=payload, headers=self.headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def get_record(self, record_id, main_location_id):
        # If GHL provides a GET endpoint, prefer it. If not, fallback to search and filter by id.
        r = requests.post(f"{GHL_BASE_URL}/records/search",
                          json={"locationId": main_location_id, "page": 1, "pageLimit": 1, "searchAfter": None},
                          headers=self.headers(), timeout=30)
        r.raise_for_status()
        # fallback simple scan — you may adapt if API has GET /records/{id}
        data = r.json()
        for rec in data.get("records", []):
            if rec.get("id") == record_id:
                return rec
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

        r = requests.put(url, json=payload, headers=self.headers(), timeout=30)

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