from fastapi import APIRouter

from app.api.schemas import (
    IncomingMessageRequest,
    IncomingMessageResponse,
    WhatsAppWebhookRequest,
    WhatsAppWebhookResponse,
)
from app.core.orchestrator import (
    process_incoming_message,
    process_whatsapp_webhook,
)

router = APIRouter(tags=["chat"])


@router.post("/messages/incoming", response_model=IncomingMessageResponse)
def incoming_message(payload: IncomingMessageRequest) -> IncomingMessageResponse:
    result = process_incoming_message(
        contact_id=payload.contact_id,
        contact_name=payload.contact_name,
        message=payload.message.strip(),
    )
    return IncomingMessageResponse(**result)


@router.post("/webhooks/whatsapp", response_model=WhatsAppWebhookResponse)
def whatsapp_webhook(payload: WhatsAppWebhookRequest) -> WhatsAppWebhookResponse:
    result = process_whatsapp_webhook(
        from_number=payload.from_number,
        from_name=payload.from_name,
        text=payload.text.strip(),
    )
    return WhatsAppWebhookResponse(**result)