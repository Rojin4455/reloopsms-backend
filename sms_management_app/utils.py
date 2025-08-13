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

import re

def format_password(location_name: str) -> str:
    # Remove leading/trailing spaces and collapse multiple spaces
    cleaned = re.sub(r'\s+', ' ', location_name.strip())
    
    # Capitalize first letter of each word to ensure at least 1 uppercase
    cleaned = cleaned.title()
    
    # Append @123 (already has special char, digits, and min length)
    password = f"{cleaned}@123"
    
    return password


def format_international(number, default_country_code="61"):
    """
    Format a phone number into international format:
    - Removes leading '+' if present
    - Removes leading zero for local numbers
    - Prepends default country code if missing
    """
    number = str(number).strip()

    # Already has country code
    if number.startswith("+"):
        return number[1:]  # remove '+' to match your API format

    # Remove all non-digit characters
    number = ''.join(filter(str.isdigit, number))

    # If number length is 9 or 10, assume local and add country code
    if len(number) <= 10:
        number = f"{default_country_code}{number.lstrip('0')}"

    return number