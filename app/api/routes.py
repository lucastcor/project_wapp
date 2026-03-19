from fastapi import APIRouter, Header, Query

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
    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    result = process_incoming_message(
        contact_id=payload.contact_id,
        contact_name=payload.contact_name,
        message=payload.message.strip(),
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
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
    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    result = process_whatsapp_webhook(
        from_number=payload.from_number,
        from_name=payload.from_name,
        text=payload.text.strip(),
        company_id=request_company["company_id"],
        company_slug=request_company["company_slug"],
    )
    return WhatsAppWebhookResponse(**result)