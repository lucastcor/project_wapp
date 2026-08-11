import hashlib
import hmac
import logging
from typing import Any, TypedDict

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.schemas import (
    IncomingMessageRequest,
    IncomingMessageResponse,
)
from app.core.config import is_production_like, settings
from app.core.credentials import extract_bearer_token
from app.core.logging import get_logger, log_event
from app.core.orchestrator import (
    process_incoming_message,
    process_whatsapp_webhook,
)
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.services.realtime_service import broadcast_event
from app.services.tenant_credential_service import (
    SCOPE_MESSAGES_WRITE,
    TenantCredential,
    TenantCredentialError,
    authenticate_tenant_credential,
)
from app.services.tenant_resolution_service import (
    TenantResolution,
    TenantResolutionError,
    resolve_api_tenant,
    resolve_channel_tenant,
)
from app.storage.repository import RuntimeRepository

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)
repository = RuntimeRepository()


class RequestCompanyScope(TypedDict):
    company_id: int | None
    company_slug: str | None


def _resolve_request_company(
    company_id: int | None = None,
    company_slug: str | None = None,
    x_company_id: str | None = None,
    x_company_slug: str | None = None,
) -> RequestCompanyScope:
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


def _extract_meta_whatsapp_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("object") != "whatsapp_business_account":
        return None

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            messages = value.get("messages")
            contacts = value.get("contacts")
            if not isinstance(messages, list) or not messages:
                continue

            metadata = value.get("metadata")
            channel_external_id = ""
            if isinstance(metadata, dict):
                channel_external_id = str(metadata.get("phone_number_id") or "").strip()

            contact_name = None
            contact_wa_id = None
            if isinstance(contacts, list) and contacts:
                first_contact = contacts[0]
                if isinstance(first_contact, dict):
                    contact_wa_id = first_contact.get("wa_id")
                    profile = first_contact.get("profile")
                    if isinstance(profile, dict):
                        contact_name = profile.get("name")

            for message in messages:
                if not isinstance(message, dict):
                    continue
                if str(message.get("type") or "").strip().lower() != "text":
                    continue
                text_payload = message.get("text")
                if not isinstance(text_payload, dict):
                    continue
                body = str(text_payload.get("body") or "").strip()
                from_number = str(message.get("from") or contact_wa_id or "").strip()
                if not body or not from_number:
                    continue
                return {
                    "event": "message.received",
                    "from_number": from_number,
                    "from_name": str(contact_name or "").strip() or None,
                    "text": body,
                    "provider_message_id": str(message.get("id") or "").strip() or None,
                    "channel_external_id": channel_external_id or None,
                }

    return None


def _extract_whatsapp_message_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if all(key in payload for key in ("event", "from_number", "text")):
        normalized_text = str(payload.get("text") or "").strip()
        normalized_number = str(payload.get("from_number") or "").strip()
        if not normalized_text or not normalized_number:
            raise HTTPException(status_code=400, detail="text and from_number must not be blank.")
        return (
            {
                "event": str(payload.get("event") or "message.received"),
                "from_number": normalized_number,
                "from_name": str(payload.get("from_name") or "").strip() or None,
                "text": normalized_text,
                "provider_message_id": str(payload.get("provider_message_id") or "").strip() or None,
                "channel_external_id": str(payload.get("channel_external_id") or "").strip() or None,
            },
            "normalized",
        )

    meta_message = _extract_meta_whatsapp_message(payload)
    if meta_message:
        return meta_message, "meta"

    return None, "ignored"


def _validate_meta_signature(raw_body: bytes, signature_header: str | None) -> None:
    app_secret = (settings.whatsapp_app_secret or "").strip()
    if not app_secret:
        raise HTTPException(status_code=503, detail="WhatsApp app secret is not configured.")

    normalized_signature = (signature_header or "").strip()
    if not normalized_signature.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Missing WhatsApp webhook signature.")

    expected_signature = hmac.new(
        app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    received_signature = normalized_signature.removeprefix("sha256=").strip()

    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook signature.")


def _authorize_webhook_payload(payload_mode: str, raw_body: bytes, signature_header: str | None) -> None:
    """Todo payload aceito precisa de origem autenticada.

    O payload normalizado interno não carrega assinatura do Meta. Ele é
    bloqueado em produção e, fora dela, exige HMAC sempre que houver
    `WHATSAPP_APP_SECRET` configurado.
    """
    if payload_mode == "meta":
        _validate_meta_signature(raw_body, signature_header)
        return

    if payload_mode != "normalized":
        return

    if is_production_like() or not settings.allow_normalized_whatsapp_webhook:
        log_event(
            logger,
            logging.WARNING,
            "api.whatsapp.webhook.normalized_payload_blocked",
        )
        raise HTTPException(
            status_code=403,
            detail="Normalized webhook payload is disabled. Send the provider payload instead.",
        )

    if (settings.whatsapp_app_secret or "").strip():
        _validate_meta_signature(raw_body, signature_header)


def _dispatch_whatsapp_auto_reply(result: dict[str, Any]) -> dict[str, Any] | None:
    if not result.get("auto_reply") or not str(result.get("reply") or "").strip():
        return None

    outbound_message_id = repository.create_outbound_message(
        contact_id=str(result["contact_id"]),
        company_id=result.get("company_id"),
        message=str(result["reply"]),
        source="whatsapp_auto_reply",
        status="pending",
    )
    try:
        dispatch_result = dispatch_outbound_message(
            outbound_message_id=outbound_message_id,
            channel="whatsapp",
            company_id=result.get("company_id"),
        )
        return {
            "outbound_message_id": outbound_message_id,
            "delivery_status": dispatch_result.get("delivery_status"),
            "provider_message_id": dispatch_result.get("provider_message_id"),
            "provider_response": dispatch_result.get("provider_response"),
            "error": None,
        }
    except OutboundDispatchError as exc:
        log_event(
            logger,
            logging.WARNING,
            "api.whatsapp.auto_reply.dispatch_failed",
            outbound_message_id=outbound_message_id,
            error_type=type(exc).__name__,
        )
        return {
            "outbound_message_id": outbound_message_id,
            "delivery_status": "failed",
            "provider_message_id": None,
            "provider_response": None,
            "error": str(exc),
        }


def _begin_whatsapp_idempotent_processing(
    *,
    normalized_payload: dict[str, Any],
    request_company: RequestCompanyScope,
    payload_mode: str,
) -> tuple[bool, dict[str, Any] | None]:
    provider_message_id = str(normalized_payload.get("provider_message_id") or "").strip()
    if not provider_message_id:
        return True, None

    status = repository.begin_inbound_message_processing(
        provider="whatsapp",
        provider_message_id=provider_message_id,
        company_id=request_company.get("company_id"),
        contact_id=str(normalized_payload.get("from_number") or "").strip() or None,
        payload_mode=payload_mode,
    )
    if status == "started":
        return True, None

    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.duplicate_ignored",
        provider_message_id=provider_message_id,
        duplicate_status=status,
        company_id=request_company.get("company_id"),
        company_slug=request_company.get("company_slug"),
    )
    return False, {
        "processed": False,
        "reason": "duplicate_provider_message_id",
        "duplicate_status": status,
        "payload_mode": payload_mode,
        "provider_message_id": provider_message_id,
    }


def _authenticate_ingest(
    *,
    authorization: str | None,
    x_tenant_token: str | None,
    requested_company_id: int | None,
    requested_company_slug: str | None,
) -> TenantResolution:
    """Resolve o tenant da ingestão a partir da credencial apresentada.

    O `company_id`/`company_slug` do chamador nunca escolhe empresa: ele só
    pode confirmar a empresa da credencial. Divergência é 403.
    """
    token = x_tenant_token or extract_bearer_token(authorization)

    if not token:
        if is_production_like() or not settings.allow_unauthenticated_ingest:
            raise HTTPException(
                status_code=401,
                detail="Tenant API credential is required for message ingestion.",
            )
        # Ergonomia de desenvolvimento, explicitamente habilitada. Mesmo aqui o
        # chamador não escolhe empresa: cai na empresa padrão.
        log_event(
            logger,
            logging.WARNING,
            "api.messages.incoming.unauthenticated",
        )
        return resolve_api_tenant()

    try:
        credential = authenticate_tenant_credential(token, required_scope=SCOPE_MESSAGES_WRITE)
    except TenantCredentialError as exc:
        # Falha fechada e sem distinguir inexistente de revogado.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if requested_company_id is not None and int(requested_company_id) != credential.company_id:
        log_event(
            logger,
            logging.WARNING,
            "api.messages.incoming.company_mismatch",
            credential_company_id=credential.company_id,
            requested_company_id=requested_company_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Requested company does not match the authenticated credential.",
        )

    resolution = _resolution_for_credential(credential)

    if requested_company_slug and requested_company_slug != resolution.company_slug:
        log_event(
            logger,
            logging.WARNING,
            "api.messages.incoming.company_mismatch",
            credential_company_id=credential.company_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Requested company does not match the authenticated credential.",
        )

    return resolution


def _resolution_for_credential(credential: TenantCredential) -> TenantResolution:
    company = repository.get_company(credential.company_id)
    if not company or not int(company.get("active") or 0):
        raise HTTPException(status_code=403, detail="Credential company is not active.")
    return TenantResolution(
        company_id=int(company["id"]),
        company_slug=str(company.get("slug") or "") or None,
        source="tenant_credential",
    )


@router.post("/messages/incoming", response_model=IncomingMessageResponse)
def incoming_message(
    payload: IncomingMessageRequest,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
    x_company_slug: str | None = Header(default=None, alias="X-Company-Slug"),
    x_tenant_token: str | None = Header(default=None, alias="X-Tenant-Token"),
    authorization: str | None = Header(default=None),
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
    try:
        tenant = _authenticate_ingest(
            authorization=authorization,
            x_tenant_token=x_tenant_token,
            requested_company_id=request_company["company_id"],
            requested_company_slug=request_company["company_slug"],
        )
    except TenantResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_company_id = tenant.company_id
    resolved_company_slug = tenant.company_slug
    log_event(
        logger,
        logging.INFO,
        "api.messages.incoming.received",
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
        tenant_source=tenant.source,
        has_contact_name=bool(payload.contact_name),
    )
    result: dict[str, Any] = process_incoming_message(
        contact_id=payload.contact_id,
        contact_name=payload.contact_name,
        message=normalized_message,
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
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
    broadcast_event(
        "new_message",
        {
            "contact_id": payload.contact_id,
            "classification": result.get("classification"),
            "handoff_required": result.get("handoff_required"),
        },
        company_id=result.get("company_id"),
    )
    return IncomingMessageResponse(**result)


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    if hub_mode != "subscribe" or not hub_challenge:
        raise HTTPException(status_code=400, detail="Invalid WhatsApp webhook verification payload.")

    configured_token = (settings.whatsapp_webhook_verify_token or "").strip()
    if not configured_token:
        raise HTTPException(status_code=503, detail="WhatsApp webhook verification token is not configured.")

    if hub_verify_token != configured_token:
        raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook verify token.")

    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.verified",
    )
    return PlainTextResponse(str(hub_challenge))


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
    x_company_slug: str | None = Header(default=None, alias="X-Company-Slug"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> Any:
    try:
        raw_body = await request.body()
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")

    normalized_payload, payload_mode = _extract_whatsapp_message_payload(payload)
    if not normalized_payload:
        log_event(
            logger,
            logging.INFO,
            "api.whatsapp.webhook.ignored",
            payload_mode=payload_mode,
        )
        return {
            "processed": False,
            "reason": "unsupported_or_non_message_event",
            "payload_mode": payload_mode,
        }

    _authorize_webhook_payload(payload_mode, raw_body, x_hub_signature_256)

    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    try:
        tenant = resolve_channel_tenant(
            provider="whatsapp",
            external_id=normalized_payload.get("channel_external_id")
            or settings.whatsapp_phone_number_id,
            requested_company_id=request_company["company_id"],
            requested_company_slug=request_company["company_slug"],
        )
    except TenantResolutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    resolved_company_id = tenant.company_id
    resolved_company_slug = tenant.company_slug
    request_company = {
        "company_id": resolved_company_id,
        "company_slug": resolved_company_slug,
    }
    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.received",
        whatsapp_event=normalized_payload["event"],
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
        tenant_source=tenant.source,
        payload_mode=payload_mode,
        has_from_name=bool(normalized_payload.get("from_name")),
    )
    should_process, duplicate_response = _begin_whatsapp_idempotent_processing(
        normalized_payload=normalized_payload,
        request_company=request_company,
        payload_mode=payload_mode,
    )
    if not should_process:
        return duplicate_response

    provider_message_id = str(normalized_payload.get("provider_message_id") or "").strip()
    try:
        result = process_whatsapp_webhook(
            from_number=str(normalized_payload["from_number"]),
            from_name=normalized_payload.get("from_name"),
            text=str(normalized_payload["text"]),
            company_id=resolved_company_id,
            company_slug=resolved_company_slug,
        )
        auto_reply_dispatch = _dispatch_whatsapp_auto_reply(result)
        log_event(
            logger,
            logging.INFO,
            "api.whatsapp.webhook.completed",
            company_id=result.get("company_id"),
            company_slug=result.get("company_slug"),
            classification=result.get("classification"),
            auto_reply=result.get("auto_reply"),
            handoff_required=result.get("handoff_required"),
            auto_reply_dispatched=bool(auto_reply_dispatch),
        )
        if provider_message_id:
            repository.mark_inbound_message_processing_completed(
                provider="whatsapp",
                provider_message_id=provider_message_id,
                company_id=resolved_company_id,
            )
        return {
            **result,
            "payload_mode": payload_mode,
            "provider_message_id": normalized_payload.get("provider_message_id"),
            "auto_reply_dispatch": auto_reply_dispatch,
        }
    except Exception as exc:
        if provider_message_id:
            repository.mark_inbound_message_processing_failed(
                provider="whatsapp",
                provider_message_id=provider_message_id,
                error=f"{type(exc).__name__}: {exc}",
                company_id=resolved_company_id,
            )
        raise
