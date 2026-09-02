"""
Tests for the TransmitSMS "Inbound SMS" path — messages arriving on a dedicated
number that the provider could not attribute to an outbound send, and which
therefore never reach a send's reply_callback.

Run with the isolated settings so this never touches the production RDS:

    python manage.py test sms_management_app.tests_inbound_routing \
        --settings=reloopsms.test_settings_isolated
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import GHLAuthCredentials, Wallet
from sms_management_app.inbound_routing import build_forward_url, to_e164
from sms_management_app.models import GHLTransmitSMSMapping, SMSMessage, WebhookLog
from sms_management_app.services import TransmitSMSService
from transmitsms.models import TransmitSMSAccount

CONTACTS_UPSERT = "/contacts/upsert"
CONVERSATIONS_SEARCH = "/conversations/search"
CONVERSATIONS_CREATE = "/conversations/"
GHL_INBOUND = "/conversations/messages/inbound"


def _resp(status_code, body=None):
    """Minimal stand-in for a requests.Response."""
    body = {} if body is None else body
    response = MagicMock()
    response.status_code = status_code
    response.text = json.dumps(body)
    response.json.return_value = body
    return response


def _routing_calls(contact_id="contact_new", conversation_id="conv_new", existing=None):
    """Fake GHL contact/conversation endpoints used by inbound_routing."""

    def handler(method, url, **kwargs):
        if url.endswith(CONTACTS_UPSERT):
            return _resp(200, {"contact": {"id": contact_id}})
        if CONVERSATIONS_SEARCH in url:
            return _resp(200, {"conversations": existing or []})
        if url.endswith(CONVERSATIONS_CREATE):
            return _resp(201, {"conversation": {"id": conversation_id}})
        raise AssertionError(f"unexpected GHL call: {method} {url}")

    return handler


def _make_account(location_id="loc_inbound_1"):
    # This signal calls out to GHL on create.
    from core import signals as core_signals

    post_save.disconnect(core_signals.sync_wallet_with_ghl, sender=GHLAuthCredentials)
    try:
        return GHLAuthCredentials.objects.create(
            user_id="user-inbound",
            access_token="tok",
            refresh_token="ref",
            expires_in=3600,
            location_id=location_id,
            location_name="Inbound Test Location",
            company_id="company_1",
        )
    finally:
        post_save.connect(core_signals.sync_wallet_with_ghl, sender=GHLAuthCredentials)


@override_settings(BASE_URL="https://channels.example.com")
class ForwardUrlTests(SimpleTestCase):
    def test_forward_url_targets_the_inbound_endpoint_for_the_account(self):
        account_id = uuid.UUID("cc2d6ca0-fff6-4b7a-bb44-f0564492cf05")
        self.assertEqual(
            build_forward_url(account_id),
            "https://channels.example.com/api/sms/transmit-sms/inbound-callback/"
            "cc2d6ca0-fff6-4b7a-bb44-f0564492cf05/",
        )

    def test_forward_url_does_not_double_slash_when_base_url_has_one(self):
        with override_settings(BASE_URL="https://channels.example.com/"):
            self.assertNotIn("//api", build_forward_url(uuid.uuid4()))


class PhoneFormattingTests(SimpleTestCase):
    def test_transmit_formats_all_normalize_to_e164(self):
        for raw in ("61421829382", "0421829382", "+61421829382", "421829382"):
            self.assertEqual(to_e164(raw), "+61421829382", msg=raw)


class InboundWebhookTests(TestCase):
    def setUp(self):
        self.ghl_account = _make_account()
        Wallet.objects.create(account=self.ghl_account, balance=100)
        self.transmit_account = TransmitSMSAccount.objects.create(
            account_name="Inbound Test",
            api_key="key",
            api_secret="secret",
            account_id="198644",
            phone_number="61430251895",
        )
        GHLTransmitSMSMapping.objects.create(
            ghl_account=self.ghl_account, transmit_account=self.transmit_account
        )
        self.url = reverse("transmit_inbound_callback", args=[self.transmit_account.id])

    def _payload(self, **overrides):
        payload = {
            "user_id": "198644",
            "rate": "10",
            "mobile": "61421829382",
            "response": "Hi Lana, I didn't realise I'm going away Thursday",
            "response_id": "153224752",
            "longcode": "61430251895",
            "datetime_entry": "2026-09-01 06:59:04",
            "is_optout": "no",
        }
        payload.update(overrides)
        return payload

    def test_unknown_number_creates_contact_and_delivers_to_ghl(self):
        """The case that was silently dropped: a contact with no prior message."""
        with patch(
            "sms_management_app.inbound_routing.ghl_request",
            side_effect=_routing_calls(contact_id="contact_abc", conversation_id="conv_abc"),
        ) as routing, patch(
            "core.ghl_auth.ghl_request", return_value=_resp(200, {"msg": "ok"})
        ) as push:
            response = self.client.get(self.url, self._payload())

        self.assertEqual(response.status_code, 200)

        sms = SMSMessage.objects.get(transmit_message_id="153224752")
        self.assertEqual(sms.direction, "inbound")
        self.assertEqual(sms.status, "delivered")
        self.assertEqual(sms.ghl_contact_id, "contact_abc")
        self.assertEqual(sms.ghl_conversation_id, "conv_abc")
        self.assertEqual(sms.from_number, "61421829382")
        self.assertEqual(sms.to_number, "61430251895")
        self.assertEqual(sms.ghl_account, self.ghl_account)

        # Contact was created, not assumed to exist.
        called = [call.args[1] for call in routing.call_args_list]
        self.assertTrue(any(u.endswith(CONTACTS_UPSERT) for u in called))
        self.assertTrue(any(u.endswith(CONVERSATIONS_CREATE) for u in called))

        # And the message actually got pushed into the conversation.
        push_body = push.call_args.kwargs["json"]
        self.assertEqual(push_body["conversationId"], "conv_abc")
        self.assertEqual(push_body["message"], self._payload()["response"])
        self.assertTrue(push.call_args.args[1].endswith(GHL_INBOUND))

    def test_webhook_hit_is_logged_even_before_processing(self):
        with patch(
            "sms_management_app.inbound_routing.ghl_request", side_effect=_routing_calls()
        ), patch("core.ghl_auth.ghl_request", return_value=_resp(200)):
            self.client.get(self.url, self._payload())

        log = WebhookLog.objects.get(webhook_type="transmit_inbound")
        self.assertEqual(log.raw_data["mobile"], "61421829382")
        self.assertEqual(log.raw_data["account_id"], str(self.transmit_account.id))

    def test_existing_conversation_is_reused_without_touching_ghl_contacts(self):
        SMSMessage.objects.create(
            ghl_account=self.ghl_account,
            transmit_account=self.transmit_account,
            message_content="earlier outbound",
            to_number="61421829382",
            from_number="61430251895",
            direction="outbound",
            ghl_conversation_id="conv_existing",
            ghl_contact_id="contact_existing",
            status="delivered",
        )

        with patch("sms_management_app.inbound_routing.ghl_request") as routing, patch(
            "core.ghl_auth.ghl_request", return_value=_resp(200)
        ):
            self.client.get(self.url, self._payload())

        routing.assert_not_called()
        sms = SMSMessage.objects.get(transmit_message_id="153224752")
        self.assertEqual(sms.ghl_conversation_id, "conv_existing")
        self.assertEqual(sms.ghl_contact_id, "contact_existing")

    def test_existing_ghl_conversation_is_not_duplicated(self):
        """Contact exists in GHL but has no message in our DB."""
        with patch(
            "sms_management_app.inbound_routing.ghl_request",
            side_effect=_routing_calls(existing=[{"id": "conv_from_ghl"}]),
        ) as routing, patch("core.ghl_auth.ghl_request", return_value=_resp(200)):
            self.client.get(self.url, self._payload())

        called = [call.args[1] for call in routing.call_args_list]
        self.assertFalse(any(u.endswith(CONVERSATIONS_CREATE) for u in called))
        sms = SMSMessage.objects.get(transmit_message_id="153224752")
        self.assertEqual(sms.ghl_conversation_id, "conv_from_ghl")

    def test_replayed_response_id_does_not_duplicate_the_message(self):
        payload = self._payload()
        with patch(
            "sms_management_app.inbound_routing.ghl_request", side_effect=_routing_calls()
        ), patch("core.ghl_auth.ghl_request", return_value=_resp(200)):
            self.client.get(self.url, payload)
            self.client.get(self.url, payload)

        self.assertEqual(SMSMessage.objects.filter(transmit_message_id="153224752").count(), 1)

    def test_post_form_encoded_is_accepted(self):
        with patch(
            "sms_management_app.inbound_routing.ghl_request", side_effect=_routing_calls()
        ), patch("core.ghl_auth.ghl_request", return_value=_resp(200)):
            response = self.client.post(self.url, self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(SMSMessage.objects.filter(transmit_message_id="153224752").exists())

    def test_payload_without_mobile_is_dropped_but_still_logged(self):
        payload = self._payload()
        payload.pop("mobile")

        with patch("sms_management_app.inbound_routing.ghl_request") as routing:
            response = self.client.get(self.url, payload)

        self.assertEqual(response.status_code, 200)
        routing.assert_not_called()
        self.assertEqual(SMSMessage.objects.count(), 0)
        self.assertEqual(WebhookLog.objects.filter(webhook_type="transmit_inbound").count(), 1)

    def test_unmapped_transmit_account_is_handled_without_error(self):
        orphan = TransmitSMSAccount.objects.create(
            account_name="No Mapping",
            api_key="k",
            api_secret="s",
            account_id="999999",
        )
        url = reverse("transmit_inbound_callback", args=[orphan.id])

        response = self.client.get(url, self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SMSMessage.objects.count(), 0)

    def test_unknown_transmit_account_is_handled_without_error(self):
        url = reverse("transmit_inbound_callback", args=[uuid.uuid4()])

        response = self.client.get(url, self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SMSMessage.objects.count(), 0)


class EditNumberOptionsTests(SimpleTestCase):
    def test_success_is_reported(self):
        with patch("sms_management_app.services.requests.post") as post:
            post.return_value = _resp(200, {"error": {"code": "SUCCESS", "description": "OK"}})
            result = TransmitSMSService().edit_number_options(
                "61430251895", "https://channels.example.com/hook/", api_key="k", api_secret="s"
            )

        self.assertTrue(result["success"])
        sent = post.call_args.kwargs["data"]
        self.assertEqual(sent["number"], "61430251895")
        self.assertEqual(sent["forward_url"], "https://channels.example.com/hook/")

    def test_api_error_is_surfaced(self):
        with patch("sms_management_app.services.requests.post") as post:
            post.return_value = _resp(
                200, {"error": {"code": "FIELD_INVALID", "description": "Invalid number"}}
            )
            result = TransmitSMSService().edit_number_options(
                "123", "https://channels.example.com/hook/", api_key="k", api_secret="s"
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Invalid number")
