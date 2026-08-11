"""Adapters de efeito externo, sempre server-side.

Regras que este módulo existe para garantir:

1. **Todo efeito passa pelo ledger.** O adapter só é chamado depois que a
   linha `pending` foi criada com `effect_key`. Se um retry recalcular a
   mesma chave e encontrar `succeeded`, o adapter não é chamado de novo.
2. **Nada de efeito externo dentro de transação reexecutável.** O ledger é
   gravado, a transação fecha, e só então o adapter roda. O resultado volta
   numa segunda escrita.
3. **O nó de IA não produz efeito.** Ele gera texto ou dado; quem envia é um
   nó de ação, com adapter e entrada própria no ledger.

Os adapters delegam aos serviços existentes: handoff, reminder, outbound e
pipeline. Nenhum caminho paralelo de envio é criado aqui.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.automations.catalog import (
    ACTION_CREATE_HANDOFF,
    ACTION_CREATE_REMINDER,
    ACTION_SEND_MESSAGE,
    ACTION_UPDATE_PIPELINE_STAGE,
)
from app.automations.exceptions import PermanentExecutionError, RetryableExecutionError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class EffectResult:
    succeeded: bool
    detail: dict[str, Any]
    failure_reason: str | None = None


EffectAdapter = Callable[[int, int, dict[str, Any]], EffectResult]


def _require_contact_id(payload: dict[str, Any], effect_label: str) -> str:
    contact_id = payload.get("contact_id")
    if contact_id is None or not str(contact_id).strip():
        raise PermanentExecutionError(
            f"{effect_label} effect requires a contact_id in the event payload."
        )
    return str(contact_id)


def _repository():
    from app.storage.repository import RuntimeRepository

    return RuntimeRepository()


def _create_handoff(company_id: int, execution_id: int, payload: dict[str, Any]) -> EffectResult:
    """Encaminha para a fila humana usando o mesmo mecanismo do orquestrador."""
    contact_id = _require_contact_id(payload, "Handoff")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise PermanentExecutionError("Handoff effect requires a reason.")

    pending_question_id = _repository().create_pending_question(
        contact_id=contact_id,
        # O evento não carrega o texto do cliente; o motivo configurado no
        # fluxo é o que a fila precisa ver.
        customer_message=f"[automação] {reason}",
        question_type="automation_handoff",
        question_for_owner=reason,
        company_id=int(company_id),
    )
    return EffectResult(
        succeeded=True,
        detail={
            "pending_question_id": int(pending_question_id),
            "priority": str(payload.get("priority") or "normal"),
        },
    )


def _create_reminder(company_id: int, execution_id: int, payload: dict[str, Any]) -> EffectResult:
    from app.services.reminder_service import (
        ReminderServiceError,
        create_internal_reminder,
    )

    contact_id = _require_contact_id(payload, "Reminder")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise PermanentExecutionError("Reminder effect requires a message.")

    try:
        reminder = create_internal_reminder(
            company_id=int(company_id),
            owner_contact_id=contact_id,
            message_text=message,
        )
    except ReminderServiceError as exc:
        # Capacidade desabilitada ou política inválida não melhora com retry.
        raise PermanentExecutionError(str(exc)) from exc
    return EffectResult(succeeded=True, detail={"reminder_id": reminder.get("id")})


def _send_message(company_id: int, execution_id: int, payload: dict[str, Any]) -> EffectResult:
    """Enfileira a mensagem. O despacho ao canal segue pelo worker existente.

    Enfileirar em vez de enviar aqui é deliberado: o job de automação fecha
    rápido e o envio herda o rate limit, o retry e a auditoria que o caminho
    de saída já tem.
    """
    contact_id = _require_contact_id(payload, "Send-message")
    text = str(payload.get("text") or "").strip()
    if not text:
        # Um nó de IA que não produziu texto não vira mensagem vazia.
        raise PermanentExecutionError(
            "Send-message effect has no text. Check the upstream AI node or the static text."
        )

    outbound_message_id = _repository().create_outbound_message(
        contact_id=contact_id,
        message=text,
        source=f"automation:execution:{int(execution_id)}",
        company_id=int(company_id),
    )
    return EffectResult(
        succeeded=True,
        detail={"outbound_message_id": int(outbound_message_id), "queued": True},
    )


def _update_pipeline_stage(
    company_id: int,
    execution_id: int,
    payload: dict[str, Any],
) -> EffectResult:
    contact_id = _require_contact_id(payload, "Pipeline-stage")
    stage = str(payload.get("stage") or "").strip()
    if not stage:
        raise PermanentExecutionError("Pipeline-stage effect requires a target stage.")

    repository = _repository()
    contact = repository.get_contact(contact_id, company_id=int(company_id))
    if contact is None:
        # O contato pertence a outra empresa ou ainda não foi persistido.
        # Tenant errado nunca vira criação: a atualização simplesmente falha.
        raise RetryableExecutionError(
            f"Contact {contact_id} is not available in this company yet."
        )

    # Classificação e resumo são preservados: este efeito move só o estágio.
    repository.update_contact_memory(
        contact_id=contact_id,
        last_classification=str(contact.get("last_classification") or ""),
        conversation_summary=str(contact.get("conversation_summary") or ""),
        pipeline_stage=stage,
        company_id=int(company_id),
    )
    return EffectResult(succeeded=True, detail={"stage": stage})


EFFECT_ADAPTERS: dict[str, EffectAdapter] = {
    ACTION_CREATE_HANDOFF: _create_handoff,
    ACTION_CREATE_REMINDER: _create_reminder,
    ACTION_SEND_MESSAGE: _send_message,
    ACTION_UPDATE_PIPELINE_STAGE: _update_pipeline_stage,
}


def apply_effect(
    *,
    kind: str,
    company_id: int,
    execution_id: int,
    payload: dict[str, Any],
) -> EffectResult:
    """Executa o efeito. O chamador já garantiu a entrada no ledger."""
    adapter = EFFECT_ADAPTERS.get(kind)
    if adapter is None:
        raise PermanentExecutionError(f"No server-side adapter for effect '{kind}'.")

    log_event(
        logger,
        logging.INFO,
        "automation.effect.applying",
        company_id=int(company_id),
        execution_id=int(execution_id),
        effect_kind=kind,
    )
    return adapter(int(company_id), int(execution_id), payload)
