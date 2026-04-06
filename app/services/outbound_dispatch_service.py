import logging
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event, mask_identifier
from app.services.whatsapp_provider import WhatsAppProvider, WhatsAppProviderError
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()
logger = get_logger(__name__)

VALID_OUTBOUND_CHANNELS = {"whatsapp", "instagram", "generic"}


class OutboundDispatchError(Exception):
    pass


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


def _send_via_provider(channel: str, outbound_message: dict[str, Any]) -> dict[str, Any]:
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
                f"Outbound provider 'whatsapp' does not support channel '{channel}'."
            )
        try:
            provider = WhatsAppProvider()
            return provider.send_message(
                contact_id=str(outbound_message["contact_id"]),
                message=str(outbound_message["message"]),
            )
        except WhatsAppProviderError as exc:
            raise OutboundDispatchError(str(exc)) from exc

    raise OutboundDispatchError(
        f"Unsupported outbound provider: {provider_name}."
    )


def dispatch_outbound_message(
    outbound_message_id: int,
    channel: str | None = None,
) -> dict[str, Any]:
    normalized_channel = _normalize_channel(channel)
    log_event(
        logger,
        logging.INFO,
        "outbound.dispatch.started",
        outbound_message_id=outbound_message_id,
        channel=normalized_channel,
    )
    outbound_message = repository.get_outbound_message(outbound_message_id)

    if not outbound_message:
        raise OutboundDispatchError("Outbound message not found.")

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

    provider_result = _send_via_provider(
        channel=normalized_channel,
        outbound_message=outbound_message,
    )

    repository.mark_outbound_message_sent(outbound_message_id)
    updated_outbound_message = repository.get_outbound_message(outbound_message_id)
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
) -> list[dict[str, Any]]:
    normalized_channel = _normalize_channel(channel)
    outbound_messages = repository.get_outbound_messages()
    pending_messages = [
        message for message in outbound_messages if message.get("status") == "pending"
    ]

    results: list[dict[str, Any]] = []
    for pending_message in pending_messages:
        results.append(
            dispatch_outbound_message(
                outbound_message_id=int(pending_message["id"]),
                channel=normalized_channel,
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
