"""Inspeção e operação de execuções.

Leitura redigida: a API de execuções é visível para viewer, e o contexto de
execução carrega identificadores de contato. `redact_context` mascara o que
não precisa aparecer numa tela de inspeção.

Operação: retry manual de dead-letter e cancelamento cooperativo. Nenhum dos
dois reexecuta efeito já concluído — o ledger decide isso, não a rota.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automations.domain import (
    EXECUTION_CANCELED,
    EXECUTION_DEAD_LETTER,
    EXECUTION_FAILED,
    EXECUTION_QUEUED,
    TERMINAL_EXECUTION_STATES,
)
from app.automations.engine import JOB_KIND_ADVANCE, process_job
from app.automations.exceptions import ExecutionNotFoundError
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.services.tenant_credential_service import mask_secret
from app.storage import automation_repository as repo

logger = get_logger(__name__)

# Chaves do contexto que são identificadores diretos. Mascaradas na leitura.
_MASKED_CONTEXT_KEYS = frozenset({"contact_id", "conversation_id", "message_id"})


def redact_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """Contexto pronto para exibição: identificadores mascarados."""
    redacted: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if key in _MASKED_CONTEXT_KEYS and value is not None:
            redacted[key] = mask_secret(str(value))
        else:
            redacted[key] = value
    return redacted


def list_executions(
    *,
    company_id: int,
    workflow_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation execution listing")
    executions = repo.list_executions(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        status=status,
        limit=limit,
    )
    return [
        {
            **{key: value for key, value in execution.items() if key != "context_json"},
            "context": redact_context(execution.get("context")),
        }
        for execution in executions
    ]


def describe_execution(*, company_id: int, execution_id: int) -> dict[str, Any]:
    """Execução com passos, jobs e efeitos. Tudo redigido."""
    resolved_company_id = require_company_id(company_id, context="automation execution read")
    execution = repo.get_execution(company_id=resolved_company_id, execution_id=execution_id)
    if execution is None:
        raise ExecutionNotFoundError(f"Execution {execution_id} not found.")

    return {
        **{key: value for key, value in execution.items() if key != "context_json"},
        "context": redact_context(execution.get("context")),
        "steps": [
            {key: value for key, value in step.items() if key != "detail_json"}
            for step in repo.list_steps(
                company_id=resolved_company_id,
                execution_id=execution_id,
            )
        ],
        "jobs": [
            {key: value for key, value in job.items() if key != "payload_json"}
            for job in repo.list_jobs(
                company_id=resolved_company_id,
                execution_id=execution_id,
            )
        ],
        "effects": [
            {key: value for key, value in effect.items() if key != "result_json"}
            for effect in repo.list_effects(
                company_id=resolved_company_id,
                execution_id=execution_id,
            )
        ],
    }


def list_dead_letter(*, company_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Dead-letter consultável, por execução."""
    resolved_company_id = require_company_id(company_id, context="automation dead letter")
    jobs = repo.list_jobs(company_id=resolved_company_id, status="dead_letter", limit=limit)
    return [
        {
            "job_id": int(job["id"]),
            "execution_id": int(job["execution_id"]),
            "job_kind": str(job["job_kind"]),
            "node_id": job.get("node_id"),
            "attempts": int(job.get("attempts") or 0),
            "last_error": job.get("last_error"),
            "updated_at": job.get("updated_at"),
        }
        for job in jobs
    ]


def retry_execution(
    *,
    company_id: int,
    execution_id: int,
    actor: str | None = None,
) -> dict[str, Any]:
    """Reenfileira uma execução que morreu.

    Retoma do nó onde parou. Efeitos já concluídos não são repetidos: o
    `effect_key` do passo é o mesmo e a reserva encontra `succeeded`.
    """
    resolved_company_id = require_company_id(company_id, context="automation retry")
    execution = repo.get_execution(company_id=resolved_company_id, execution_id=execution_id)
    if execution is None:
        raise ExecutionNotFoundError(f"Execution {execution_id} not found.")

    status = str(execution["status"])
    if status not in {EXECUTION_DEAD_LETTER, EXECUTION_FAILED}:
        return {
            "retried": False,
            "reason": f"execution_status_is_{status}",
            "execution_id": execution_id,
        }

    node_id = execution.get("current_node_id")
    if not node_id:
        return {"retried": False, "reason": "no_resume_point", "execution_id": execution_id}

    job_id = repo.enqueue_job(
        company_id=resolved_company_id,
        execution_id=execution_id,
        job_kind=JOB_KIND_ADVANCE,
        node_id=str(node_id),
        max_attempts=settings.automation_max_attempts,
    )
    repo.update_execution(
        company_id=resolved_company_id,
        execution_id=execution_id,
        status=EXECUTION_QUEUED,
    )
    log_event(
        logger,
        logging.WARNING,
        "automation.execution.retried",
        company_id=resolved_company_id,
        execution_id=execution_id,
        job_id=job_id,
        actor_id=actor,
    )
    return {"retried": True, "execution_id": execution_id, "job_id": job_id}


def cancel_execution(
    *,
    company_id: int,
    execution_id: int,
    actor: str | None = None,
) -> dict[str, Any]:
    """Cancelamento cooperativo.

    Marca os jobs pendentes e fecha a execução. O job que já estiver em voo
    termina o passo e não reagenda — matar no meio deixaria efeito
    parcialmente aplicado sem registro.
    """
    resolved_company_id = require_company_id(company_id, context="automation cancel")
    execution = repo.get_execution(company_id=resolved_company_id, execution_id=execution_id)
    if execution is None:
        raise ExecutionNotFoundError(f"Execution {execution_id} not found.")

    if str(execution["status"]) in TERMINAL_EXECUTION_STATES:
        return {
            "canceled": False,
            "reason": f"execution_already_{execution['status']}",
            "execution_id": execution_id,
        }

    affected = repo.request_job_cancellation(
        company_id=resolved_company_id,
        execution_id=execution_id,
    )
    repo.update_execution(
        company_id=resolved_company_id,
        execution_id=execution_id,
        status=EXECUTION_CANCELED,
        failure_reason="canceled_by_operator",
        finished=True,
    )
    log_event(
        logger,
        logging.WARNING,
        "automation.execution.canceled",
        company_id=resolved_company_id,
        execution_id=execution_id,
        canceled_jobs=affected,
        actor_id=actor,
    )
    return {"canceled": True, "execution_id": execution_id, "canceled_jobs": affected}


def run_due_jobs(*, limit: int = 10, lease_owner: str | None = None) -> dict[str, Any]:
    """Processa jobs prontos. É o corpo de um ciclo do worker.

    Exposto como função para que o teste de concorrência possa rodar dois
    "workers" no mesmo processo e provar que nenhum job é processado duas
    vezes.
    """
    owner = lease_owner or repo.new_lease_owner()
    reports: list[dict[str, Any]] = []

    for _ in range(limit):
        job = repo.claim_job(
            lease_owner=owner,
            lease_seconds=settings.automation_job_lease_seconds,
        )
        if job is None:
            break
        report = process_job(job=job)
        reports.append(
            {
                "job_id": int(job["id"]),
                "execution_id": report.execution_id,
                "status": report.status,
                "node_id": report.node_id,
                "effect_reused": report.effect_reused,
            }
        )

    return {"lease_owner": owner, "processed": len(reports), "reports": reports}


def recover_stalled_work() -> dict[str, Any]:
    """Reconciliador: devolve à fila o que ficou preso.

    Cobre três casos: job com lease vencido (worker morreu), portão reservado
    e abandonado, e portão que passou da validade.
    """
    recovered_jobs = repo.recover_expired_leases()
    recovered_gates = repo.recover_expired_gate_claims()
    expired_gates = repo.expire_overdue_gates()

    if recovered_jobs or recovered_gates or expired_gates:
        log_event(
            logger,
            logging.WARNING,
            "automation.reconciler.recovered",
            recovered_jobs=recovered_jobs,
            recovered_gates=recovered_gates,
            expired_gates=expired_gates,
        )
    return {
        "recovered_jobs": recovered_jobs,
        "recovered_gate_claims": recovered_gates,
        "expired_gates": expired_gates,
    }
