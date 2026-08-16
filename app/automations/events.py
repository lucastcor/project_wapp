"""Eventos canônicos de automação.

Um evento é um **fato já persistido**, não um pedido. Ele carrega apenas o
necessário para decidir um caminho, sempre redigido: identificadores,
classificação, confiança, estágio e sinalizadores. Nunca o texto da mensagem
nem dados do contato — o `graph_json`, os logs e a API de execuções são
visíveis para todo operador do tenant, e conteúdo de conversa não precisa
estar lá para o fluxo funcionar.

`event_key` é determinístico e único dentro da empresa: reentregar o mesmo
fato encontra o evento existente em vez de criar um segundo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

EVENT_SCHEMA_VERSION = "automation-event/1"

EVENT_MESSAGE_PROCESSED = "message.processed"
EVENT_HANDOFF_CREATED = "handoff.created"
EVENT_APPOINTMENT_BOOKED = "appointment.booked"
EVENT_APPOINTMENT_CANCELED = "appointment.canceled"
EVENT_REMINDER_DUE = "reminder.due"
EVENT_MANUAL_TRIGGERED = "manual.triggered"
EVENT_SCHEDULE_TRIGGERED = "schedule.triggered"

CANONICAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_MESSAGE_PROCESSED,
        EVENT_HANDOFF_CREATED,
        EVENT_APPOINTMENT_BOOKED,
        EVENT_APPOINTMENT_CANCELED,
        EVENT_REMINDER_DUE,
        EVENT_MANUAL_TRIGGERED,
        EVENT_SCHEDULE_TRIGGERED,
    }
)

# Gatilho do catálogo que cada evento aciona.
TRIGGER_BY_EVENT_TYPE: dict[str, str] = {
    EVENT_MESSAGE_PROCESSED: "trigger.messageProcessed",
    EVENT_HANDOFF_CREATED: "trigger.handoffCreated",
    EVENT_APPOINTMENT_BOOKED: "trigger.appointmentBooked",
    EVENT_MANUAL_TRIGGERED: "trigger.manual",
    EVENT_SCHEDULE_TRIGGERED: "trigger.schedule",
}

# Campos permitidos no payload. Uma chave fora desta lista é descartada na
# construção — a redação é por allowlist, não por blocklist, porque a
# blocklist erra sempre que alguém acrescenta um campo novo.
ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "message_id",
        "contact_id",
        "conversation_id",
        "appointment_id",
        "reminder_id",
        "handoff_id",
        "classification",
        "confidence",
        "action",
        "pipeline_stage",
        "customer_profile",
        "handoff_required",
        "reply_source",
        "has_relevant_source",
        "auto_reply_sent",
        "channel",
        "occurred_at",
        "triggered_by",
    }
)


class EventPayloadTooLargeError(Exception):
    pass


@dataclass(frozen=True)
class CanonicalEvent:
    company_id: int
    event_type: str
    event_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = EVENT_SCHEMA_VERSION

    def serialized(self) -> str:
        return json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def byte_size(self) -> int:
        return len(self.serialized().encode("utf-8"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "event_type": self.event_type,
            "event_key": self.event_key,
            "schema_version": self.schema_version,
            "payload": dict(self.payload),
        }


def redact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Mantém apenas os campos permitidos, já normalizados."""
    source = payload or {}
    redacted: dict[str, Any] = {}
    for key in sorted(ALLOWED_PAYLOAD_KEYS):
        if key not in source:
            continue
        value = source[key]
        if value is None:
            continue
        if isinstance(value, (bool, int, float)):
            redacted[key] = value
        else:
            # Limite defensivo: nenhum campo canônico é longo.
            redacted[key] = str(value)[:200]
    return redacted


def build_event_key(*, company_id: int, event_type: str, subject: str) -> str:
    """Chave determinística do fato.

    `subject` identifica o fato dentro do tipo (id da mensagem, do
    agendamento, da janela agendada). Duas emissões do mesmo fato produzem a
    mesma chave e, portanto, um único evento.
    """
    raw = f"{int(company_id)}|{event_type}|{subject}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def build_event(
    *,
    company_id: int,
    event_type: str,
    subject: str,
    payload: dict[str, Any] | None = None,
    max_bytes: int = 16_000,
) -> CanonicalEvent:
    """Constrói o evento canônico, redigido e dentro do limite de tamanho."""
    if event_type not in CANONICAL_EVENT_TYPES:
        raise ValueError(f"Unknown canonical event type: {event_type}")

    event = CanonicalEvent(
        company_id=int(company_id),
        event_type=event_type,
        event_key=build_event_key(
            company_id=company_id,
            event_type=event_type,
            subject=subject,
        ),
        payload=redact_payload(payload),
    )
    size = event.byte_size()
    if size > max_bytes:
        raise EventPayloadTooLargeError(
            f"Automation event payload is {size} bytes; the limit is {max_bytes}."
        )
    return event
