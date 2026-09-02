"""
Routing for TransmitSMS "Inbound SMS" — messages that arrive on a dedicated
virtual number without being attributed to any outbound send.

TransmitSMS splits incoming traffic into two streams:
  * SMS Replies  — attributed to an outbound message, delivered to that send's
                   `reply_callback` (see GHLIntegrationService.send_outbound_sms).
  * Inbound SMS  — not attributed to anything, delivered *only* to the number's
                   `forward_url`. There is no message_ref to look a conversation
                   up from, so the GHL contact has to be resolved by phone number.

https://developers.kudosity.com/reference/webhooks-1
"""
import logging

from django.conf import settings

from core.ghl_auth import ghl_request

from .utils import format_international

logger = logging.getLogger(__name__)

GHL_API = "https://services.leadconnectorhq.com"

# GHL versions the two APIs separately.
CONTACTS_API_VERSION = "2021-07-28"
CONVERSATIONS_API_VERSION = "2021-04-15"


class InboundResolutionError(Exception):
    """Raised when a GHL contact or conversation could not be resolved."""


def build_forward_url(transmit_account_id) -> str:
    """
    Number-level inbound webhook URL for a TransmitSMS account.

    The account id is carried in the path rather than resolved from the payload's
    `longcode`, because the same number can appear under more than one
    TransmitSMSAccount row and would be ambiguous.
    """
    base_url = (settings.BASE_URL or "").rstrip("/")
    return f"{base_url}/api/sms/transmit-sms/inbound-callback/{transmit_account_id}/"


def to_e164(number) -> str:
    """TransmitSMS sends bare MSISDNs (61414829252); GHL expects +61414829252."""
    return f"+{format_international(number)}"


def _headers(ghl_account, version):
    return {
        "Authorization": f"Bearer {ghl_account.access_token}",
        "Version": version,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def upsert_contact(ghl_account, phone) -> str:
    """Find or create the GHL contact for a phone number. Returns the contact id."""
    resp = ghl_request(
        "POST",
        f"{GHL_API}/contacts/upsert",
        json={"locationId": ghl_account.location_id, "phone": to_e164(phone)},
        headers=_headers(ghl_account, CONTACTS_API_VERSION),
        auth_credentials=ghl_account,
    )
    if resp.status_code not in (200, 201):
        raise InboundResolutionError(
            f"contact upsert failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    body = resp.json() if resp.text else {}
    contact_id = (body.get("contact") or {}).get("id") or body.get("id")
    if not contact_id:
        raise InboundResolutionError(f"contact upsert returned no id: {str(body)[:300]}")
    return contact_id


def get_or_create_conversation(ghl_account, contact_id) -> str:
    """Find the contact's existing conversation, creating one if there isn't any."""
    headers = _headers(ghl_account, CONVERSATIONS_API_VERSION)

    search = ghl_request(
        "GET",
        f"{GHL_API}/conversations/search",
        params={"locationId": ghl_account.location_id, "contactId": contact_id},
        headers=headers,
        auth_credentials=ghl_account,
    )
    if search.status_code == 200 and search.text:
        conversations = (search.json() or {}).get("conversations") or []
        if conversations:
            existing = conversations[0].get("id")
            if existing:
                return existing

    create = ghl_request(
        "POST",
        f"{GHL_API}/conversations/",
        json={"locationId": ghl_account.location_id, "contactId": contact_id},
        headers=headers,
        auth_credentials=ghl_account,
    )
    if create.status_code not in (200, 201):
        raise InboundResolutionError(
            f"conversation create failed (HTTP {create.status_code}): {create.text[:300]}"
        )

    body = create.json() if create.text else {}
    conversation_id = (body.get("conversation") or {}).get("id") or body.get("id")
    if not conversation_id:
        raise InboundResolutionError(
            f"conversation create returned no id: {str(body)[:300]}"
        )
    return conversation_id


def resolve_contact_and_conversation(ghl_account, phone):
    """
    Resolve (contact_id, conversation_id) for an inbound message from `phone`.

    Reuses the ids from any earlier message with this contact to avoid two API
    calls on every inbound, falling back to GHL when the pair is unknown.
    """
    from .models import SMSMessage

    normalized = format_international(phone)
    previous = (
        SMSMessage.objects.filter(ghl_account=ghl_account)
        .filter(from_number__contains=normalized)
        .exclude(ghl_conversation_id__isnull=True)
        .exclude(ghl_conversation_id="")
        .order_by("-created_at")
        .first()
    )
    if previous is None:
        previous = (
            SMSMessage.objects.filter(ghl_account=ghl_account)
            .filter(to_number__contains=normalized)
            .exclude(ghl_conversation_id__isnull=True)
            .exclude(ghl_conversation_id="")
            .order_by("-created_at")
            .first()
        )
    if previous is not None:
        return previous.ghl_contact_id, previous.ghl_conversation_id

    contact_id = upsert_contact(ghl_account, phone)
    return contact_id, get_or_create_conversation(ghl_account, contact_id)
