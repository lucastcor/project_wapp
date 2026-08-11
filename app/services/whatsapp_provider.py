

from typing import Any

from app.core.config import settings


class WhatsAppProviderError(Exception):
    pass


class WhatsAppProvider:
    """WhatsApp Cloud API client scoped to one tenant's business number.

    The access token stays in environment configuration, but the sending
    ``phone_number_id`` is resolved per company from
    ``whatsapp_channel_bindings`` so Tenant B's reply can never leave through
    Tenant A's number.
    """

    def __init__(self, *, company_id: int | None = None) -> None:
        self.base_url = settings.whatsapp_api_base_url
        self.api_token = settings.whatsapp_api_token
        self.company_id = company_id
        self.phone_number_id = self._resolve_phone_number_id(company_id)

    @staticmethod
    def _resolve_phone_number_id(company_id: int | None) -> str | None:
        if company_id is None:
            # No tenant context: fall back to the single configured number.
            return settings.whatsapp_phone_number_id

        from app.storage.repository import RuntimeRepository

        binding = RuntimeRepository().get_active_whatsapp_binding_for_company(int(company_id))
        if binding:
            return str(binding["phone_number_id"])
        return None

    def _validate_configuration(self) -> None:
        missing_fields: list[str] = []

        if not self.base_url:
            missing_fields.append("WHATSAPP_API_BASE_URL")
        if not self.api_token:
            missing_fields.append("WHATSAPP_API_TOKEN")

        if missing_fields:
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured. Missing: "
                + ", ".join(missing_fields)
            )

        if not self.phone_number_id:
            if self.company_id is not None:
                raise WhatsAppProviderError(
                    f"Company {self.company_id} has no single active WhatsApp channel binding; "
                    "outbound delivery is blocked until exactly one is configured."
                )
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured. Missing: WHATSAPP_PHONE_NUMBER_ID"
            )

    def _build_url(self) -> str:
        # Re-checked rather than asserted: assertions are stripped under -O, and
        # a missing phone_number_id here would silently build a wrong-tenant URL.
        self._validate_configuration()
        return f"{str(self.base_url).rstrip('/')}/{self.phone_number_id}/messages"

    def send_message(self, contact_id: str, message: str) -> dict[str, Any]:
        self._validate_configuration()

        contact = contact_id.strip()
        text = message.strip()
        if not contact:
            raise WhatsAppProviderError("contact_id must be a non-empty string.")
        if not text:
            raise WhatsAppProviderError("message must be a non-empty string.")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": contact,
            "type": "text",
            "text": {
                "body": text,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        try:
            import requests
        except ModuleNotFoundError as exc:
            raise WhatsAppProviderError(
                "The 'requests' package is required for the WhatsApp provider runtime. Install it to enable real WhatsApp dispatch."
            ) from exc

        try:
            response = requests.post(
                self._build_url(),
                headers=headers,
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise WhatsAppProviderError(f"Failed to send WhatsApp message: {exc}") from exc

        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = response.text
            raise WhatsAppProviderError(
                f"WhatsApp API returned status {response.status_code}: {error_payload}"
            )

        try:
            provider_response = response.json()
        except ValueError as exc:
            raise WhatsAppProviderError("WhatsApp API did not return valid JSON.") from exc

        message_id = None
        messages = provider_response.get("messages")
        if isinstance(messages, list) and messages:
            first_message = messages[0]
            if isinstance(first_message, dict):
                message_id = first_message.get("id")

        return {
            "channel": "whatsapp",
            "contact_id": contact,
            "message": text,
            "provider_message_id": message_id,
            "provider_response": provider_response,
        }
