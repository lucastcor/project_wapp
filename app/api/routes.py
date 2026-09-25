import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

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
from app.core.logging import get_logger, log_event
from app.core.orchestrator import (
    process_incoming_message,
)
from app.services.admin_auth_service import secrets_match
from app.services.realtime_service import publish_incoming_message_event
from app.storage.channel_delivery_repository import record_delivery_status
from app.storage.inbound_repository import enqueue_batch
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


def _extract_meta_whatsapp_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("object") != "whatsapp_business_account":
        return []

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return []

    extracted: list[dict[str, Any]] = []
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

            names_by_sender = {}
            for contact in contacts if isinstance(contacts, list) else []:
                if isinstance(contact, dict) and isinstance(contact.get("profile"), dict):
                    names_by_sender[str(contact.get("wa_id") or "")] = contact["profile"].get("name")

            for message in messages:
                if not isinstance(message, dict):
                    continue
                from_number = str(message.get("from") or "").strip()
                if not from_number:
                    continue
                content = _normalize_meta_message_content(message)
                if content is None:
                    continue
                context = message.get("context")
                raw_timestamp = str(message.get("timestamp") or "").strip()
                received_at = _unix_timestamp_iso(raw_timestamp)
                extracted.append({
                    "received_at": received_at,
                    "event": "message.received",
                    "from_number": from_number,
                    "from_name": str(names_by_sender.get(from_number) or "").strip() or None,
                    "text": content["text"],
                    "content_type": content["content_type"],
                    "reply_id": content.get("reply_id"),
                    "media_type": content.get("media_type"),
                    "context_message_id": (
                        str(context.get("id") or "").strip() or None if isinstance(context, dict) else None
                    ),
                    "provider_message_id": str(message.get("id") or "").strip() or None,
                    "phone_number_id": phone_number_id,
                })

    return extracted


_META_DELIVERY_STATUSES = frozenset({"sent", "delivered", "read", "failed"})


def _unix_timestamp_iso(raw_timestamp: str) -> str | None:
    """Timestamp Unix do payload em ISO UTC; valor absurdo vira None, nunca 500."""
    if not raw_timestamp.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(raw_timestamp), UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _extract_meta_whatsapp_statuses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Callbacks de status de mensagens enviadas (W05), só de payload Meta assinado."""
    if payload.get("object") != "whatsapp_business_account":
        return []
    extracted: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict) or not isinstance(value.get("statuses"), list):
                continue
            metadata = value.get("metadata")
            phone_number_id = (
                str(metadata.get("phone_number_id") or "").strip() or None if isinstance(metadata, dict) else None
            )
            for status_event in value["statuses"]:
                if not isinstance(status_event, dict):
                    continue
                status = str(status_event.get("status") or "").strip().lower()
                provider_message_id = str(status_event.get("id") or "").strip()
                if status not in _META_DELIVERY_STATUSES or not provider_message_id:
                    continue
                error_code = None
                errors = status_event.get("errors")
                if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                    raw_code = errors[0].get("code")
                    error_code = int(str(raw_code)) if str(raw_code or "").isdigit() else None
                raw_timestamp = str(status_event.get("timestamp") or "").strip()
                extracted.append({
                    "phone_number_id": phone_number_id,
                    "provider_message_id": provider_message_id,
                    "status": status,
                    "provider_timestamp": _unix_timestamp_iso(raw_timestamp),
                    # Só o código: `error_data` pode carregar dado do destinatário.
                    "error_code": error_code,
                })
    return extracted


def _resolve_status_company(phone_number_id: str | None) -> int | None:
    """Empresa do callback pelo binding ativo; sem binding, o callback é ignorado."""
    normalized = str(phone_number_id or "").strip()
    if not normalized:
        return None
    binding = repository.get_whatsapp_binding_by_phone_number_id(normalized)
    if not binding or not int(binding.get("company_active") or 0):
        return None
    return int(binding["company_id"])


# Eventos sem pedido do cliente: não viram job nem resposta.
_IGNORED_META_MESSAGE_TYPES = frozenset({"reaction", "system", "ephemeral", "request_welcome"})


def _unsupported_content(message_type: str) -> dict[str, Any]:
    media_type = re.sub(r"[^a-z_]", "", message_type)[:32] or "unknown"
    return {"content_type": "unsupported", "text": f"[{media_type}]", "media_type": media_type}


def _normalize_meta_message_content(message: dict[str, Any]) -> dict[str, Any] | None:
    """Texto utilizável de uma mensagem Meta, ou None para eventos ignorados.

    Áudio, imagem e outros tipos não somem mais em silêncio: entram como
    `unsupported` para o orquestrador orientar o cliente (W07).
    """
    message_type = str(message.get("type") or "").strip().lower()
    if message_type == "text":
        text_payload = message.get("text")
        body = str(text_payload.get("body") or "").strip() if isinstance(text_payload, dict) else ""
        return {"content_type": "text", "text": body} if body else None

    if message_type == "interactive":
        interactive = message.get("interactive")
        reply = None
        if isinstance(interactive, dict):
            reply = interactive.get("button_reply") or interactive.get("list_reply")
        if isinstance(reply, dict):
            reply_id = str(reply.get("id") or "").strip() or None
            title = str(reply.get("title") or "").strip() or reply_id
            if title:
                return {"content_type": "interactive", "text": title, "reply_id": reply_id}
        return _unsupported_content(message_type)

    if message_type == "button":
        button = message.get("button")
        if isinstance(button, dict):
            payload = str(button.get("payload") or "").strip() or None
            label = str(button.get("text") or "").strip() or payload
            if label:
                return {"content_type": "button", "text": label, "reply_id": payload}
        return _unsupported_content(message_type)

    if not message_type or message_type in _IGNORED_META_MESSAGE_TYPES:
        return None
    return _unsupported_content(message_type)


def _is_normalized_whatsapp_payload(payload: dict[str, Any]) -> bool:
    return all(key in payload for key in ("event", "from_number", "text"))


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
            content_type=str(payload.get("content_type") or "text"),
            reply_id=str(payload.get("reply_id") or "").strip() or None,
            media_type=str(payload.get("media_type") or "").strip() or None,
            context_message_id=str(payload.get("context_message_id") or "").strip() or None,
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

    # Validate the entire batch before committing any item. Invalid signatures,
    # fields or bindings never result in a partially accepted batch.
    def persist_batch() -> dict[str, Any]:
        candidates = _extract_meta_whatsapp_messages(payload) if payload_mode == "meta" else (
            [payload] if payload_mode == "normalized" else []
        )
        items = []
        rejected_count = 0
        first_rejection: HTTPException | None = None
        for candidate in candidates:
            try:
                message = _parse_normalized_whatsapp_payload(candidate)
                if not message.get("provider_message_id"):
                    raise HTTPException(status_code=422, detail="provider_message_id is required for durable webhook ingress.")
                scope = _resolve_meta_company(candidate.get("phone_number_id")) if payload_mode == "meta" else _resolve_request_company(
                    company_id=company_id, company_slug=company_slug,
                    x_company_id=x_company_id, x_company_slug=x_company_slug,
                )
                if scope["company_id"] is None:
                    raise HTTPException(status_code=422, detail="Select a company for normalized webhook ingress.")
            except HTTPException as exc:
                # Payload Meta assinado pode trazer vários números: um item de
                # número não vinculado ou inválido não derruba os demais (a Meta
                # reenviaria o lote inteiro até desistir). Nada é gravado para
                # o item recusado; lote sem nenhum item válido continua falhando.
                if payload_mode != "meta":
                    raise
                rejected_count += 1
                first_rejection = first_rejection or exc
                log_event(logger, logging.WARNING, "api.whatsapp.webhook.item_rejected", status_code=exc.status_code)
                continue
            items.append({**message, "received_at": candidate.get("received_at"), **scope, "payload_mode": payload_mode})
        # Status de números vinculados são gravados antes de uma eventual recusa
        # do lote de mensagens; senão um item inválido descartaria todos eles.
        status_updates = 0
        if payload_mode == "meta":
            for status_event in _extract_meta_whatsapp_statuses(payload):
                status_company_id = _resolve_status_company(status_event["phone_number_id"])
                if status_company_id is None:
                    continue
                recorded = record_delivery_status(
                    company_id=status_company_id,
                    provider_message_id=status_event["provider_message_id"],
                    status=status_event["status"],
                    provider_timestamp=status_event["provider_timestamp"],
                    error_code=status_event["error_code"],
                )
                status_updates += int(bool(recorded["recorded"]))
        if candidates and not items and first_rejection is not None:
            raise first_rejection
        if not items:
            return {"processed": False, "reason": "unsupported_or_non_message_event", "payload_mode": payload_mode,
                    "status_updates": status_updates}
        jobs = enqueue_batch(items)
        return {"accepted": True, "processed": False, "payload_mode": payload_mode,
                "queued_count": sum(not job["duplicate"] for job in jobs), "jobs": jobs,
                "status_updates": status_updates, "rejected_count": rejected_count}

    # The small DB transaction runs off the event loop. No LLM or delivery work
    # occurs here; a successful response means the messages are durably stored.
    return await run_in_threadpool(persist_batch)
