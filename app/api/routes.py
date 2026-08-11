import logging
import hashlib
import hmac
from typing import Any, TypedDict

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app.api.ingress_security import (
    INTERNAL_INGRESS_HEADER,
    authenticate_internal_ingress,
    authorize_normalized_whatsapp_ingress,
    internal_ingress_enforced,
)
from app.api.schemas import (
    IncomingMessageRequest,
    IncomingMessageResponse,
    NormalizedWhatsAppPayload,
)
from app.core.config import settings
from app.core.orchestrator import (
    process_incoming_message,
    process_whatsapp_webhook,
)
from app.core.logging import get_logger, log_event
from app.services.admin_auth_service import secrets_match
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.services.realtime_service import publish_incoming_message_event
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
    """Resolve the requested tenant against persisted companies.

    A caller-supplied company must exist; the request is rejected instead of
    silently falling back to the default company, which would let an arbitrary
    caller write into another tenant's scope.
    """
    requested_company_id = company_id
    if requested_company_id is None and x_company_id:
        normalized_header = x_company_id.strip()
        if normalized_header:
            try:
                requested_company_id = int(normalized_header)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="X-Company-Id must be an integer.",
                ) from exc

    requested_company_slug = company_slug or x_company_slug
    if isinstance(requested_company_slug, str):
        requested_company_slug = requested_company_slug.strip() or None

    if requested_company_id is None and not requested_company_slug:
        return {"company_id": None, "company_slug": None}

    company = None
    if requested_company_id is not None:
        company = repository.get_company(requested_company_id)
    elif requested_company_slug:
        company = repository.get_company_by_slug(requested_company_slug)

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    return {
        "company_id": int(company["id"]),
        "company_slug": str(company["slug"]),
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

            # phone_number_id is the provider's stable identifier for the
            # receiving business number and is the only trusted tenant selector
            # in a signed payload. display_phone_number is presentation data and
            # is deliberately not used for identity.
            metadata = value.get("metadata")
            phone_number_id = None
            if isinstance(metadata, dict):
                phone_number_id = str(metadata.get("phone_number_id") or "").strip() or None

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
                    "phone_number_id": phone_number_id,
                }

    return None


def _is_normalized_whatsapp_payload(payload: dict[str, Any]) -> bool:
    return all(key in payload for key in {"event", "from_number", "text"})


def _parse_normalized_whatsapp_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_text = str(payload.get("text") or "").strip()
    normalized_number = str(payload.get("from_number") or "").strip()
    if not normalized_text or not normalized_number:
        raise HTTPException(status_code=400, detail="text and from_number must not be blank.")

    try:
        validated = NormalizedWhatsAppPayload(
            event=str(payload.get("event") or "message.received"),
            from_number=normalized_number,
            from_name=str(payload.get("from_name") or "").strip() or None,
            text=normalized_text,
            provider_message_id=str(payload.get("provider_message_id") or "").strip() or None,
        )
    except ValidationError as exc:
        # Report which field failed and why, never the offending value itself.
        detail = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "error": str(error.get("type")),
            }
            for error in exc.errors(include_url=False)
        ]
        raise HTTPException(status_code=422, detail=detail) from exc

    return validated.model_dump()


def _resolve_meta_company(phone_number_id: str | None) -> RequestCompanyScope:
    """Resolve the tenant of a signed Meta event from its phone_number_id only.

    A valid signature proves the payload came from Meta; it does not authorize a
    tenant. The company therefore comes exclusively from the server-controlled
    `whatsapp_channel_bindings` table, and caller-supplied company selectors are
    ignored in Meta mode. Unknown, ambiguous, inactive or missing bindings fail
    closed — the default company is never used as a fallback.
    """
    normalized_phone_number_id = str(phone_number_id or "").strip()
    if not normalized_phone_number_id:
        _log_meta_binding_denied("missing_phone_number_id")
        raise HTTPException(
            status_code=403,
            detail="WhatsApp payload does not identify a bound business number.",
        )

    binding = repository.get_whatsapp_binding_by_phone_number_id(normalized_phone_number_id)
    if not binding:
        # Covers unknown, inactive and ambiguous (multiple active) bindings.
        _log_meta_binding_denied("unbound_phone_number_id")
        raise HTTPException(
            status_code=403,
            detail="WhatsApp business number is not bound to an active company.",
        )

    if not int(binding.get("company_active") or 0):
        _log_meta_binding_denied("inactive_company")
        raise HTTPException(
            status_code=403,
            detail="WhatsApp business number is not bound to an active company.",
        )

    return {
        "company_id": int(binding["company_id"]),
        "company_slug": str(binding["company_slug"]),
    }


def _log_meta_binding_denied(reason: str) -> None:
    log_event(
        logger,
        logging.WARNING,
        "api.whatsapp.webhook.tenant_binding_denied",
        denial_reason=reason,
    )


def _detect_whatsapp_payload_mode(payload: dict[str, Any]) -> str:
    """Classify the payload shape before any authentication decision is made."""
    if _is_normalized_whatsapp_payload(payload):
        return "normalized"
    if payload.get("object") == "whatsapp_business_account":
        return "meta"
    return "ignored"


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


@router.post("/messages/incoming", response_model=IncomingMessageResponse)
def incoming_message(
    payload: IncomingMessageRequest,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
    x_company_slug: str | None = Header(default=None, alias="X-Company-Slug"),
    x_internal_ingress_secret: str | None = Header(default=None, alias=INTERNAL_INGRESS_HEADER),
    authorization: str | None = Header(default=None),
) -> IncomingMessageResponse:
    ingress_auth_mode = authenticate_internal_ingress(
        header_secret=x_internal_ingress_secret,
        authorization=authorization,
        require_credentials=internal_ingress_enforced(),
        surface="messages_incoming",
    )

    normalized_message = payload.message.strip()
    if not normalized_message:
        raise HTTPException(status_code=400, detail="message must not be blank.")

    request_company = _resolve_request_company(
        company_id=company_id,
        company_slug=company_slug,
        x_company_id=x_company_id,
        x_company_slug=x_company_slug,
    )
    resolved_company_id = request_company["company_id"]
    resolved_company_slug = request_company["company_slug"]
    log_event(
        logger,
        logging.INFO,
        "api.messages.incoming.received",
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
        ingress_auth_mode=ingress_auth_mode,
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
    publish_incoming_message_event(result, contact_id=payload.contact_id)
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

    if not secrets_match(hub_verify_token, configured_token):
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
    x_internal_ingress_secret: str | None = Header(default=None, alias=INTERNAL_INGRESS_HEADER),
    authorization: str | None = Header(default=None),
) -> Any:
    try:
        raw_body = await request.body()
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")

    payload_mode = _detect_whatsapp_payload_mode(payload)

    # Authenticate before any parsing, persistence or orchestration side effect.
    if payload_mode == "meta":
        _validate_meta_signature(raw_body, x_hub_signature_256)
    elif payload_mode == "normalized":
        authorize_normalized_whatsapp_ingress(
            header_secret=x_internal_ingress_secret,
            authorization=authorization,
        )

    normalized_payload: dict[str, Any] | None = None
    # Kept out of the validated message model: it is Meta-only tenant metadata,
    # never a field a normalized-ingress caller can supply.
    meta_phone_number_id: str | None = None
    if payload_mode == "normalized":
        normalized_payload = _parse_normalized_whatsapp_payload(payload)
    elif payload_mode == "meta":
        meta_message = _extract_meta_whatsapp_message(payload)
        if meta_message:
            meta_phone_number_id = meta_message.get("phone_number_id")
            normalized_payload = _parse_normalized_whatsapp_payload(meta_message)

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

    if payload_mode == "meta":
        # Meta mode ignores caller-supplied company selectors entirely: the
        # tenant comes from the signed payload's phone_number_id binding.
        request_company = _resolve_meta_company(meta_phone_number_id)
    else:
        # Normalized mode is authenticated by the internal ingress secret, whose
        # holder is a trusted internal component, so it may select a tenant
        # explicitly. The company must still exist (404 otherwise).
        request_company = _resolve_request_company(
            company_id=company_id,
            company_slug=company_slug,
            x_company_id=x_company_id,
            x_company_slug=x_company_slug,
        )
    resolved_company_id = request_company["company_id"]
    resolved_company_slug = request_company["company_slug"]
    log_event(
        logger,
        logging.INFO,
        "api.whatsapp.webhook.received",
        whatsapp_event=normalized_payload["event"],
        company_id=resolved_company_id,
        company_slug=resolved_company_slug,
        payload_mode=payload_mode,
        tenant_source="channel_binding" if payload_mode == "meta" else "request_scope",
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
