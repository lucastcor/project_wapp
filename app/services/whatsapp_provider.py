

from typing import Any

from app.core.config import settings


class WhatsAppProviderError(Exception):
    pass


class WhatsAppProvider:
    def __init__(self) -> None:
        self.base_url = settings.whatsapp_api_base_url
        self.api_token = settings.whatsapp_api_token
        self.phone_number_id = settings.whatsapp_phone_number_id

    def _validate_configuration(self) -> None:
        missing_fields: list[str] = []

        if not self.base_url:
            missing_fields.append("WHATSAPP_API_BASE_URL")
        if not self.api_token:
            missing_fields.append("WHATSAPP_API_TOKEN")
        if not self.phone_number_id:
            missing_fields.append("WHATSAPP_PHONE_NUMBER_ID")

        if missing_fields:
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured. Missing: "
                + ", ".join(missing_fields)
            )

    def _build_url(self) -> str:
        # `assert` some com `python -O`, e o que sobraria é um f-string com
        # `None` no meio da URL do provider: a validação viraria formatação
        # silenciosa contra um endpoint inexistente.
        if self.base_url is None or self.phone_number_id is None:
            raise WhatsAppProviderError(
                "WhatsApp provider is not fully configured: base_url and phone_number_id are required."
            )
        return f"{self.base_url.rstrip('/')}/{self.phone_number_id}/messages"

    def send_message(self, contact_id: str, message: str) -> dict[str, Any]:
        self._validate_configuration()

        contact = contact_id.strip()
        text = message.strip()
        if not contact:
            raise WhatsAppProviderError("contact_id must be a non-empty string.")
        if not text:
            raise WhatsAppProviderError("message must be a non-empty string.")

        payload = {
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
