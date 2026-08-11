"""Construção dos eventos canônicos a partir dos fatos do runtime.

Módulo fino de propósito. Ele existe para que o orquestrador não precise
conhecer o formato do evento nem a flag do motor, e para que o payload
redigido seja construído num único lugar — a redação por allowlist só protege
se ninguém montar o dicionário à mão em outro arquivo.

Nenhuma função aqui levanta: automação é pós-orquestração e não pode
interromper o atendimento.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automations.events import (
    EVENT_APPOINTMENT_BOOKED,
    EVENT_APPOINTMENT_CANCELED,
    EVENT_HANDOFF_CREATED,
    EVENT_MANUAL_TRIGGERED,
    EVENT_MESSAGE_PROCESSED,
    EVENT_REMINDER_DUE,
    EVENT_SCHEDULE_TRIGGERED,
    build_event,
)
from app.core.config import settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)


def _safe_build(
    *,
    company_id: int,
    event_type: str,
    subject: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Constrói o evento ou devolve `None` registrando o motivo."""
    if not settings.automation_engine_enabled:
        return None
    try:
        event = build_event(
            company_id=company_id,
            event_type=event_type,
            subject=subject,
            payload=payload,
            max_bytes=settings.automation_max_event_bytes,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "automation.trigger.build_failed",
            company_id=company_id,
            automation_event_type=event_type,
            error_type=type(exc).__name__,
        )
        return None
    return {
        "event_key": event.event_key,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "payload": event.payload,
    }


def build_message_processed_event(
    *,
    company_id: int,
    message_id: int,
    contact_id: str,
    classification: str,
    confidence: float,
    action: str,
    pipeline_stage: str | None,
    customer_profile: str | None,
    handoff_required: bool,
    reply_source: str,
    has_relevant_source: bool,
    auto_reply_sent: bool,
) -> dict[str, Any] | None:
    """Evento de mensagem processada.

    `auto_reply_sent` é o campo que o validador usa para detectar conflito: o
    fluxo sabe que o caminho legado já respondeu e um envio imediato
    duplicaria a mensagem. O texto da mensagem e o da resposta **não** entram
    — o fluxo decide por classificação, estágio e sinalizadores.
    """
    return _safe_build(
        company_id=company_id,
        event_type=EVENT_MESSAGE_PROCESSED,
        subject=f"message:{int(message_id)}",
        payload={
            "message_id": message_id,
            "contact_id": contact_id,
            "classification": classification,
            "confidence": round(float(confidence), 4),
            "action": action,
            "pipeline_stage": pipeline_stage,
            "customer_profile": customer_profile,
            "handoff_required": bool(handoff_required),
            "reply_source": reply_source,
            "has_relevant_source": bool(has_relevant_source),
            "auto_reply_sent": bool(auto_reply_sent),
        },
    )


def emit_handoff_created(
    *,
    company_id: int,
    handoff_id: int,
    contact_id: str,
    question_type: str | None = None,
) -> dict[str, Any]:
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_HANDOFF_CREATED,
        subject=f"handoff:{int(handoff_id)}",
        payload={
            "handoff_id": handoff_id,
            "contact_id": contact_id,
            "action": question_type,
            "handoff_required": True,
        },
    )


def emit_appointment_booked(
    *,
    company_id: int,
    appointment_id: int | str,
    contact_id: str | None = None,
) -> dict[str, Any]:
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_APPOINTMENT_BOOKED,
        subject=f"appointment:{appointment_id}",
        payload={"appointment_id": appointment_id, "contact_id": contact_id},
    )


def emit_appointment_canceled(
    *,
    company_id: int,
    appointment_id: int | str,
    contact_id: str | None = None,
) -> dict[str, Any]:
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_APPOINTMENT_CANCELED,
        subject=f"appointment:{appointment_id}",
        payload={"appointment_id": appointment_id, "contact_id": contact_id},
    )


def emit_reminder_due(
    *,
    company_id: int,
    reminder_id: int | str,
    contact_id: str | None = None,
) -> dict[str, Any]:
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_REMINDER_DUE,
        subject=f"reminder:{reminder_id}",
        payload={"reminder_id": reminder_id, "contact_id": contact_id},
    )


def emit_manual_trigger(
    *,
    company_id: int,
    workflow_id: int,
    triggered_by: str,
    contact_id: str | None = None,
    nonce: str,
) -> dict[str, Any]:
    """Disparo manual.

    `nonce` vem do idempotency key da requisição: dois cliques com a mesma
    chave produzem um evento, e o operador que quer disparar de novo manda uma
    chave nova.
    """
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_MANUAL_TRIGGERED,
        subject=f"manual:{int(workflow_id)}:{nonce}",
        payload={"contact_id": contact_id, "triggered_by": triggered_by},
    )


def emit_schedule_trigger(
    *,
    company_id: int,
    workflow_id: int,
    window_key: str,
) -> dict[str, Any]:
    """Janela agendada. `window_key` identifica a janela, não o instante."""
    from app.services.automation_event_service import emit_event

    return emit_event(
        company_id=company_id,
        event_type=EVENT_SCHEDULE_TRIGGERED,
        subject=f"schedule:{int(workflow_id)}:{window_key}",
        payload={"triggered_by": "scheduler"},
    )
