import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event, mask_identifier
from app.services.billing_service import BillingLimitError, require_usage_capacity
from app.services.whatsapp_delivery_policy import decide_whatsapp_delivery
from app.services.whatsapp_provider import WhatsAppProvider, WhatsAppProviderError
from app.storage.channel_delivery_repository import link_orphan_delivery_events
from app.storage.ownership_repository import is_human_owned
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
logger = get_logger(__name__)

VALID_OUTBOUND_CHANNELS = {"whatsapp", "instagram", "generic"}


class OutboundDispatchError(Exception):
    """Falha de envio. `kind` decide se repetir é seguro.

    `transient` e `configuration` voltam com backoff; `permanent` não muda com
    retry; `uncertain` pode já ter sido aceita pela Meta, e repetir duplicaria
    a mensagem — as duas últimas vão direto para dead letter.
    """

    RETRYABLE_KINDS = frozenset({"transient", "configuration"})

    def __init__(self, message: str, *, kind: str = "transient", code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE_KINDS

    @property
    def diagnostic(self) -> str:
        return f"{self.kind}:{self.code if self.code is not None else '-'}:{self}"


def _normalize_channel(channel: str | None) -> str:
    normalized = (channel or settings.outbound_default_channel or "generic").strip().lower()
    if normalized not in VALID_OUTBOUND_CHANNELS:
        raise OutboundDispatchError(
            f"Unsupported outbound channel: {channel}. Supported channels: {', '.join(sorted(VALID_OUTBOUND_CHANNELS))}."
        )
    return normalized


def _build_dispatch_result(
    outbound_message: dict[str, Any],
    channel: str,
    delivery_status: str,
    provider_message_id: str | None = None,
    provider_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "outbound_message_id": outbound_message["id"],
        "contact_id": outbound_message["contact_id"],
        "channel": channel,
        "message": outbound_message["message"],
        "source": outbound_message["source"],
        "delivery_status": delivery_status,
        "provider_message_id": provider_message_id,
        "provider_response": provider_response,
    }


# Envio automático (resposta do bot, lembrete, fila de encaixe, automação) não sai
# para conversa assumida por uma pessoa (P06, H01). Texto escrito pelo operador
# (`owner_resolution`, `manual_booking_confirmation`) e confirmação de SAIR/VOLTAR
# saem normalmente.
_AUTOMATIC_SOURCES = frozenset({"whatsapp_auto_reply", "reminder_customer"})
_AUTOMATIC_SOURCE_PREFIXES = ("appointment_queue_", "automation:")


def is_automatic_source(source: Any) -> bool:
    normalized = str(source or "").strip()
    return normalized in _AUTOMATIC_SOURCES or normalized.startswith(_AUTOMATIC_SOURCE_PREFIXES)


def _withheld_by_human_ownership(outbound_message: dict[str, Any]) -> bool:
    company_id = outbound_message.get("company_id")
    if company_id is None or not is_automatic_source(outbound_message.get("source")):
        return False
    try:
        return is_human_owned(company_id=int(company_id), contact_id=str(outbound_message["contact_id"]))
    except Exception as exc:
        # Sem saber se há humano na conversa, a mensagem não sai agora.
        raise OutboundDispatchError(
            f"ownership_check_unavailable:{type(exc).__name__}", kind="transient"
        ) from exc


def _send_via_provider(channel: str, outbound_message: dict[str, Any]) -> dict[str, Any]:
    if _withheld_by_human_ownership(outbound_message):
        raise OutboundDispatchError("blocked:human_owned", kind="permanent")
    provider_name = (settings.outbound_provider or "mock").strip().lower()

    if provider_name == "mock":
        return {
            "channel": channel,
            "contact_id": outbound_message["contact_id"],
            "message": outbound_message["message"],
            "provider_message_id": None,
            "provider_response": {
                "provider": "mock",
                "status": "accepted",
            },
        }

    if provider_name == "whatsapp":
        if channel != "whatsapp":
            raise OutboundDispatchError(
                f"Outbound provider 'whatsapp' does not support channel '{channel}'.",
                kind="configuration",
            )
        # Janela de 24 h, template, opt-out e consentimento são decididos aqui,
        # no envio efetivo: uma recusa registrada depois do enfileiramento
        # ainda barra a mensagem (P05, W03/W06).
        try:
            decision = decide_whatsapp_delivery(outbound_message, now=datetime.now(UTC))
        except Exception as exc:
            # Sem decisão de janela/opt-out não há envio; a falha entra no
            # retry controlado em vez de deixar a mensagem presa em processing.
            raise OutboundDispatchError(
                f"delivery_policy_unavailable:{type(exc).__name__}", kind="transient"
            ) from exc
        if decision.mode == "blocked":
            raise OutboundDispatchError(f"blocked:{decision.reason}", kind="permanent")
        try:
            # The sending number is bound to the message's own company, so a
            # reply never leaves through another tenant's WhatsApp number.
            message_company_id = outbound_message.get("company_id")
            provider = WhatsAppProvider(
                company_id=int(message_company_id) if message_company_id is not None else None,
            )
            if decision.mode == "template" and decision.template is not None:
                result = provider.send_template(
                    str(outbound_message["contact_id"]),
                    template_name=str(decision.template["name"]),
                    language_code=str(decision.template["language_code"]),
                    parameters=decision.parameters,
                )
                result["provider_response"] = {
                    "response": result.get("provider_response"),
                    "template": {
                        "id": decision.template["id"],
                        "name": decision.template["name"],
                        "language_code": decision.template["language_code"],
                        "purpose": decision.purpose,
                    },
                }
                return result
            return provider.send_message(
                contact_id=str(outbound_message["contact_id"]),
                message=str(outbound_message["message"]),
            )
        except WhatsAppProviderError as exc:
            raise OutboundDispatchError(str(exc), kind=exc.kind, code=exc.code) from exc

    raise OutboundDispatchError(
        f"Unsupported outbound provider: {provider_name}.",
        kind="configuration",
    )


def dispatch_outbound_message(
    outbound_message_id: int,
    channel: str | None = None,
    *,
    company_id: int | None = None,
) -> dict[str, Any]:
    normalized_channel = _normalize_channel(channel)
    log_event(
        logger,
        logging.INFO,
        "outbound.dispatch.started",
        outbound_message_id=outbound_message_id,
        channel=normalized_channel,
    )
    supports_claims = hasattr(repository, "claim_outbound_message")
    outbound_message = repository.get_outbound_message(
        outbound_message_id,
        company_id=company_id,
    )

    if not outbound_message:
        raise OutboundDispatchError("Outbound message not found.", kind="permanent")

    if outbound_message.get("status") == "sent":
        log_event(
            logger,
            logging.INFO,
            "outbound.dispatch.skipped",
            outbound_message_id=outbound_message_id,
            channel=normalized_channel,
            reason="already_sent",
        )
        return _build_dispatch_result(
            outbound_message=outbound_message,
            channel=normalized_channel,
            delivery_status="already_sent",
        )

    claimed_message = None
    if supports_claims:
        claimed_message = repository.claim_outbound_message(
            outbound_message_id,
            company_id=company_id,
            channel=normalized_channel,
            claim_ttl_seconds=settings.outbound_claim_ttl_seconds,
            max_attempts=settings.outbound_max_attempts,
        )
        if claimed_message is None:
            current = repository.get_outbound_message(
                outbound_message_id,
                company_id=company_id,
            )
            if current and current.get("status") == "sent":
                return _build_dispatch_result(
                    outbound_message=current,
                    channel=normalized_channel,
                    delivery_status="already_sent",
                )
            if current and current.get("status") == "dead_letter":
                raise OutboundDispatchError("Outbound message exhausted its retry policy.", kind="permanent")
            if current and current.get("status") == "retry":
                raise OutboundDispatchError("Outbound message is waiting for its next retry.", kind="transient")
            if current and current.get("status") == "processing":
                return _build_dispatch_result(
                    outbound_message=current,
                    channel=normalized_channel,
                    delivery_status="already_processing",
                )
            raise OutboundDispatchError("Outbound message could not be claimed.")
        outbound_message = claimed_message

    try:
        provider_result = _send_via_provider(
            channel=normalized_channel,
            outbound_message=outbound_message,
        )
    except OutboundDispatchError as exc:
        if claimed_message is not None:
            attempt_count = int(claimed_message.get("attempt_count") or 1)
            max_attempts = int(
                claimed_message.get("max_attempts") or settings.outbound_max_attempts
            )
            retry_at = None
            if exc.retryable and attempt_count < max_attempts:
                base_seconds = max(1, settings.outbound_retry_base_seconds)
                capped_seconds = min(
                    max(base_seconds, settings.outbound_retry_max_seconds),
                    base_seconds * (2 ** max(0, attempt_count - 1)),
                )
                jitter_seconds = secrets.randbelow(max(1, capped_seconds // 4 + 1))
                retry_at = (
                    datetime.now(UTC) + timedelta(seconds=capped_seconds + jitter_seconds)
                ).isoformat()
            final_status = repository.mark_outbound_message_failed(
                outbound_message_id,
                claim_token=str(claimed_message["claim_token"]),
                error=exc.diagnostic,
                retry_at=retry_at,
            )
            log_event(
                logger,
                logging.WARNING,
                "outbound.dispatch.failed",
                outbound_message_id=outbound_message_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                retry_at=retry_at,
                final_status=final_status,
                error_type=type(exc).__name__,
                error_kind=exc.kind,
                error_code=exc.code,
            )
        raise

    outbound_company_id = outbound_message.get("company_id")
    if claimed_message is None:
        repository.mark_outbound_message_sent(
            outbound_message_id,
            company_id=outbound_company_id,
        )
    else:
        repository.mark_outbound_message_sent(
            outbound_message_id,
            company_id=outbound_company_id,
            claim_token=str(claimed_message["claim_token"]),
            channel=normalized_channel,
            provider_message_id=provider_result.get("provider_message_id"),
            provider_response=provider_result.get("provider_response"),
        )
        if outbound_company_id is not None and provider_result.get("provider_message_id"):
            # Callback de status que chegou antes deste registro ganha vínculo agora.
            try:
                link_orphan_delivery_events(
                    company_id=int(outbound_company_id),
                    provider_message_id=str(provider_result["provider_message_id"]),
                    outbound_message_id=int(outbound_message_id),
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "outbound.dispatch.delivery_event_link_failed",
                    outbound_message_id=outbound_message_id,
                    error_type=type(exc).__name__,
                )
    updated_outbound_message = repository.get_outbound_message(
        outbound_message_id,
        company_id=outbound_company_id,
    )
    if not updated_outbound_message:
        raise OutboundDispatchError("Outbound message not found after dispatch update.")

    log_event(
        logger,
        logging.INFO,
        "outbound.dispatch.completed",
        outbound_message_id=outbound_message_id,
        channel=normalized_channel,
        contact_id=mask_identifier(str(updated_outbound_message["contact_id"])),
        source=updated_outbound_message.get("source"),
    )
    return _build_dispatch_result(
        outbound_message=updated_outbound_message,
        channel=normalized_channel,
        delivery_status="sent",
        provider_message_id=provider_result.get("provider_message_id"),
        provider_response=provider_result.get("provider_response"),
    )


def dispatch_pending_outbound_messages(
    channel: str | None = None,
    *,
    company_id: int | None = None,
) -> list[dict[str, Any]]:
    normalized_channel = _normalize_channel(channel)
    outbound_messages = repository.get_outbound_messages(company_id=company_id)
    pending_messages = [
        message
        for message in outbound_messages
        if message.get("status") in {"pending", "retry"}
    ]
    if company_id is not None and pending_messages:
        try:
            require_usage_capacity(
                company_id=int(company_id),
                metric="outbound_campaigns",
            )
        except BillingLimitError as exc:
            raise OutboundDispatchError(str(exc)) from exc

    results: list[dict[str, Any]] = []
    # Explicit loop, not a comprehension: each iteration performs an external
    # send. Keeping it statement-shaped makes the side effect obvious.
    for pending_message in pending_messages:
        results.append(  # noqa: PERF401
            dispatch_outbound_message(
                outbound_message_id=int(pending_message["id"]),
                channel=normalized_channel,
                company_id=company_id,
            )
        )

    log_event(
        logger,
        logging.INFO,
        "outbound.dispatch_bulk.completed",
        channel=normalized_channel,
        pending_count=len(pending_messages),
        dispatched_count=len(results),
    )
    return results
