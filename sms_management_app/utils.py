import re
from django.conf import settings

def format_phone_number(phone, country_code=None):
    """Format phone number to E.164 international format"""
    # Remove all non-digit characters
    phone = re.sub(r'\D', '', phone)
    
    # If phone starts with +, remove it (we'll add it back)
    if phone.startswith('+'):
        phone = phone[1:]
    
    # If country code is provided and phone doesn't start with country code
    if country_code and not phone.startswith(country_code):
        # Remove leading 0 if present (common in local formats)
        if phone.startswith('0'):
            phone = phone[1:]
        phone = country_code + phone
    
    # Add + prefix
    if not phone.startswith('+'):
        phone = '+' + phone
    
    return phone

def validate_phone_number(phone):
    """Basic validation for E.164 phone numbers"""
    pattern = r'^\+[1-9]\d{1,14}'
    return bool(re.match(pattern, phone))

def get_callback_url(endpoint):
    """Generate full callback URL"""
    base_url = settings.BASE_URL.rstrip('/')
    return f"{base_url}/api/{endpoint.lstrip('/')}"
