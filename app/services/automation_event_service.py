"""Emissão de eventos e dispatch do outbox.

O padrão é outbox transacional: quem persiste o fato grava o evento e a
entrada de outbox juntos, e o dispatch acontece depois. Duas consequências
que importam:

1. **Emitir nunca derruba o atendimento.** `emit_event` engole falha e
   registra: se o Automation Studio estiver com problema, a resposta ao
   cliente continua saindo. Automação é pós-orquestração, não caminho
   crítico.
2. **Reentrega não duplica.** `event_key` é único por empresa e a inscrição é
   única por `(workflow, enrollment_key)`. O mesmo fato entregue duas vezes
   produz um evento e uma execução.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automations.domain import enrollment_key, parse_graph
from app.automations.engine import JOB_KIND_ADVANCE
from app.automations.events import (
    EVENT_SCHEMA_VERSION,
    TRIGGER_BY_EVENT_TYPE,
    build_event,
)
from app.automations.semantics import find_trigger
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.storage import automation_repository as repo

logger = get_logger(__name__)

OUTBOX_PENDING = "pending"
OUTBOX_DISPATCHED = "dispatched"
OUTBOX_FAILED = "failed"
OUTBOX_SKIPPED = "skipped"


def emit_event(
    *,
    company_id: int,
    event_type: str,
    subject: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra o fato no evento e no outbox, na mesma transação.

    Devolve `{"emitted": bool, "event_id": int | None, "reason": str | None}`.
    Nunca levanta: o chamador é o caminho de atendimento.
    """
    if not settings.automation_engine_enabled:
        return {"emitted": False, "event_id": None, "reason": "engine_disabled"}

    try:
        resolved_company_id = require_company_id(company_id, context="automation event emit")
        event = build_event(
            company_id=resolved_company_id,
            event_type=event_type,
            subject=subject,
            payload=payload,
            max_bytes=settings.automation_max_event_bytes,
        )
        recorded = repo.record_event_with_outbox(
            company_id=resolved_company_id,
            event_key=event.event_key,
            event_type=event.event_type,
            schema_version=EVENT_SCHEMA_VERSION,
            payload=event.payload,
        )
        log_event(
            logger,
            logging.INFO,
            "automation.event.emitted",
            company_id=resolved_company_id,
            automation_event_type=event_type,
            event_created=recorded["created"],
        )
        return {
            "emitted": True,
            "event_id": int(recorded["event_id"]),
            "created": bool(recorded["created"]),
            "reason": None,
        }
    except Exception as exc:
        # Automação com problema não pode derrubar o atendimento.
        log_event(
            logger,
            logging.ERROR,
            "automation.event.emit_failed",
            company_id=company_id,
            automation_event_type=event_type,
            error_type=type(exc).__name__,
        )
        return {"emitted": False, "event_id": None, "reason": type(exc).__name__}


def dispatch_event(*, company_id: int, event_id: int) -> dict[str, Any]:
    """Inscreve os workflows ativos cujo gatilho casa com o evento.

    Só deployment `active` entra. Pausado não é inscrito — é assim que pausar
    bloqueia novas entradas sem tocar em quem já está esperando.
    """
    resolved_company_id = require_company_id(company_id, context="automation dispatch")
    event = repo.get_event(company_id=resolved_company_id, event_id=event_id)
    if event is None:
        return {"enrolled": [], "reason": "event_not_found"}

    trigger_type = TRIGGER_BY_EVENT_TYPE.get(str(event["event_type"]))
    if trigger_type is None:
        return {"enrolled": [], "reason": "event_type_has_no_trigger"}

    deployments = repo.list_active_deployments_for_trigger(
        company_id=resolved_company_id,
        trigger_type=trigger_type,
    )

    enrolled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for deployment in deployments:
        workflow_id = int(deployment["workflow_id"])
        version_id = int(deployment["version_id"])
        version = repo.get_version(company_id=resolved_company_id, version_id=version_id)
        if version is None:
            skipped.append({"workflow_id": workflow_id, "reason": "version_missing"})
            continue

        graph = parse_graph(version.get("graph"))
        trigger_node = find_trigger(graph)
        if trigger_node is None:
            skipped.append({"workflow_id": workflow_id, "reason": "trigger_missing"})
            continue

        key = enrollment_key(workflow_id=workflow_id, event_key=str(event["event_key"]))
        execution_id = repo.create_execution(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            version_id=version_id,
            enrollment_key=key,
            event_id=int(event["id"]),
            context=_build_execution_context(event),
            current_node_id=trigger_node.id,
        )
        if execution_id is None:
            # Reentrega do mesmo evento: a execução já existe.
            skipped.append({"workflow_id": workflow_id, "reason": "already_enrolled"})
            continue

        job_id = repo.enqueue_job(
            company_id=resolved_company_id,
            execution_id=execution_id,
            job_kind=JOB_KIND_ADVANCE,
            node_id=trigger_node.id,
            max_attempts=settings.automation_max_attempts,
        )
        enrolled.append(
            {
                "workflow_id": workflow_id,
                "execution_id": execution_id,
                "version_id": version_id,
                "job_id": job_id,
            }
        )

    log_event(
        logger,
        logging.INFO,
        "automation.event.dispatched",
        company_id=resolved_company_id,
        event_id=event_id,
        enrolled_count=len(enrolled),
        skipped_count=len(skipped),
    )
    return {"enrolled": enrolled, "skipped": skipped, "reason": None}


def _build_execution_context(event: dict[str, Any]) -> dict[str, Any]:
    """Contexto inicial da execução: o payload redigido do evento.

    Nada é acrescentado aqui. O que o fluxo pode ler é exatamente o que o
    evento canônico carrega — nenhum caminho lateral para conteúdo de
    conversa ou dado de contato.
    """
    payload = dict(event.get("payload") or {})
    return {
        **payload,
        "event_type": str(event["event_type"]),
        "event_key": str(event["event_key"]),
    }


def dispatch_pending_events(*, limit: int = 20, lease_owner: str | None = None) -> dict[str, Any]:
    """Consome o outbox: reserva, inscreve e marca o resultado.

    Chamado pelo worker. Uma entrada que falha volta para `pending` com
    `available_at` no futuro; o worker tenta de novo mais tarde.
    """
    owner = lease_owner or repo.new_lease_owner("outbox")
    entries = repo.claim_outbox_entries(
        lease_owner=owner,
        limit=limit,
        lease_seconds=settings.automation_job_lease_seconds,
    )

    dispatched = 0
    failed = 0
    enrolled_total = 0
    for entry in entries:
        company_id = int(entry["company_id"])
        outbox_id = int(entry["id"])
        try:
            result = dispatch_event(company_id=company_id, event_id=int(entry["event_id"]))
            enrolled_total += len(result.get("enrolled") or [])
            repo.finish_outbox_entry(
                company_id=company_id,
                outbox_id=outbox_id,
                status=OUTBOX_DISPATCHED,
            )
            dispatched += 1
        except Exception as exc:
            failed += 1
            repo.finish_outbox_entry(
                company_id=company_id,
                outbox_id=outbox_id,
                status=OUTBOX_PENDING,
                error=str(exc)[:500],
            )
            log_event(
                logger,
                logging.ERROR,
                "automation.outbox.dispatch_failed",
                company_id=company_id,
                outbox_id=outbox_id,
                error_type=type(exc).__name__,
            )

    return {
        "claimed": len(entries),
        "dispatched": dispatched,
        "failed": failed,
        "enrolled": enrolled_total,
    }
