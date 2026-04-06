import logging

from fastapi import APIRouter, Header, HTTPException, Query

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
from app.core.logging import get_logger, log_event

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)


def _resolve_request_company(
    company_id: int | None = None,
    company_slug: str | None = None,
    x_company_id: str | None = None,
    x_company_slug: str | None = None,
) -> dict[str, int | str | None]:
    resolved_company_id = company_id
    if resolved_company_id is None and x_company_id:
        try:
            resolved_company_id = int(x_company_id.strip())
        except ValueError:
            resolved_company_id = None

    resolved_company_slug = company_slug or x_company_slug
    if isinstance(resolved_company_slug, str):
        resolved_company_slug = resolved_company_slug.strip() or None

    return {
        "company_id": resolved_company_id,
        "company_slug": resolved_company_slug,
    }


@router.post("/messages/incoming", response_model=IncomingMessageResponse)
def incoming_message(
    payload: IncomingMessageRequest,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
    x_company_slug: str | None = Header(default=None, alias="X-Company-Slug"),
) -> IncomingMessageResponse:
    normalized_message = payload.message.strip()
    if not normalized_message:
        raise HTTPException(status_code=400, detail="message must not be blank.")

    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    log_event(
        logger,
        logging.INFO,
        "api.messages.incoming.received",
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
        has_contact_name=bool(payload.contact_name),
    )
    result = process_incoming_message(
        contact_id=payload.contact_id,
        contact_name=payload.contact_name,
        message=normalized_message,
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
    )
    log_event(
        logger,
        logging.INFO,
        "api.messages.incoming.completed",
        company_id=result.get("company_id"),
        company_slug=result.get("company_slug"),
        classification=result.get("classification"),
        auto_reply=result.get("auto_reply"),
        handoff_required=result.get("handoff_required"),
    )
    return IncomingMessageResponse(**result)


@router.post("/webhooks/whatsapp", response_model=WhatsAppWebhookResponse)
def whatsapp_webhook(
    payload: WhatsAppWebhookRequest,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
    x_company_slug: str | None = Header(default=None, alias="X-Company-Slug"),
) -> WhatsAppWebhookResponse:
    normalized_text = payload.text.strip()
    if not normalized_text:
        raise HTTPException(status_code=400, detail="text must not be blank.")

    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.received",
        whatsapp_event=payload.event,
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
        has_from_name=bool(payload.from_name),
    )
    result = process_whatsapp_webhook(
        from_number=payload.from_number,
        from_name=payload.from_name,
        text=normalized_text,
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
    )
    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.completed",
        company_id=result.get("company_id"),
        company_slug=result.get("company_slug"),
        classification=result.get("classification"),
        auto_reply=result.get("auto_reply"),
        handoff_required=result.get("handoff_required"),
    )
    return WhatsAppWebhookResponse(**result)
