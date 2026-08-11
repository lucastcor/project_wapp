"""Portões humanos duráveis.

Máquina de estados:

    awaiting_human -> processing -> completed | failed
                   \\-> expired | canceled

Garantias:

- **claim atômico**: dois operadores clicando ao mesmo tempo produzem um
  vencedor; o outro recebe 409;
- **release idempotente**: decidir duas vezes não muda a decisão nem cria um
  segundo job de retomada;
- **workflow pausado não executa o portão**: a decisão é registrada e
  auditada, mas a retomada só é enfileirada quando o deployment está ativo —
  pausar precisa realmente parar a execução;
- **lease vencido volta para a fila**, pelo reconciliador;
- **autorização por empresa** vem do escopo já autorizado da requisição, e o
  `decided_by` é o principal real, nunca um rótulo de header.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automations.domain import (
    DECISION_APPROVED,
    DECISION_REJECTED,
    DEPLOYMENT_ACTIVE,
    EXECUTION_AWAITING_HUMAN,
    EXECUTION_QUEUED,
    GATE_AWAITING_HUMAN,
    GATE_COMPLETED,
    GATE_PROCESSING,
    TERMINAL_EXECUTION_STATES,
    parse_graph,
)
from app.automations.engine import JOB_KIND_RESUME_GATE
from app.automations.exceptions import ApprovalGateError
from app.automations.semantics import approval_handle, resolve_next
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.storage import automation_repository as repo

logger = get_logger(__name__)


def list_pending_gates(*, company_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Fila de aprovações da empresa, com o contexto mínimo para decidir."""
    resolved_company_id = require_company_id(company_id, context="automation gate listing")
    gates = repo.list_gates(
        company_id=resolved_company_id,
        status=GATE_AWAITING_HUMAN,
        limit=limit,
    )
    enriched: list[dict[str, Any]] = []
    for gate in gates:
        execution = repo.get_execution(
            company_id=resolved_company_id,
            execution_id=int(gate["execution_id"]),
        )
        workflow = (
            repo.get_workflow(
                company_id=resolved_company_id,
                workflow_id=int(execution["workflow_id"]),
            )
            if execution
            else None
        )
        enriched.append(
            {
                **{key: value for key, value in gate.items() if key != "payload_json"},
                "workflow_id": (execution or {}).get("workflow_id"),
                "workflow_name": (workflow or {}).get("name"),
            }
        )
    return enriched


def claim_gate(
    *,
    company_id: int,
    gate_id: int,
    claimed_by: str,
) -> dict[str, Any]:
    """Reserva o portão para este operador. Falha se outro chegou antes."""
    resolved_company_id = require_company_id(company_id, context="automation gate claim")
    gate = repo.get_gate(company_id=resolved_company_id, gate_id=gate_id)
    if gate is None:
        raise ApprovalGateError(f"Approval gate {gate_id} not found.")

    claimed = repo.claim_gate(
        company_id=resolved_company_id,
        gate_id=gate_id,
        claimed_by=claimed_by,
        lease_seconds=settings.automation_gate_lease_seconds,
    )
    if not claimed:
        current = repo.get_gate(company_id=resolved_company_id, gate_id=gate_id)
        raise ApprovalGateError(
            f"Approval gate {gate_id} is already {(current or {}).get('status')!s}."
        )

    log_event(
        logger,
        logging.INFO,
        "automation.gate.claimed",
        company_id=resolved_company_id,
        gate_id=gate_id,
        actor_id=claimed_by,
    )
    return repo.get_gate(company_id=resolved_company_id, gate_id=gate_id) or {}


def decide_gate(
    *,
    company_id: int,
    gate_id: int,
    decision: str,
    decided_by: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Registra a decisão e retoma a execução pela alça correspondente.

    Idempotente: uma segunda chamada devolve `already_decided` sem criar um
    segundo job. Sem isso, um duplo clique produziria dois avanços do mesmo
    passo.
    """
    resolved_company_id = require_company_id(company_id, context="automation gate decide")
    if decision not in {DECISION_APPROVED, DECISION_REJECTED}:
        raise ApprovalGateError(f"Invalid decision: {decision}")

    gate = repo.get_gate(company_id=resolved_company_id, gate_id=gate_id)
    if gate is None:
        raise ApprovalGateError(f"Approval gate {gate_id} not found.")

    status = str(gate["status"])
    if status == GATE_COMPLETED:
        return {
            "decided": False,
            "reason": "already_decided",
            "gate_id": gate_id,
            "decision": gate.get("decision"),
        }
    if status not in {GATE_AWAITING_HUMAN, GATE_PROCESSING}:
        raise ApprovalGateError(f"Approval gate {gate_id} is {status} and cannot be decided.")

    execution_id = int(gate["execution_id"])
    execution = repo.get_execution(company_id=resolved_company_id, execution_id=execution_id)
    if execution is None:
        raise ApprovalGateError(f"Execution {execution_id} for gate {gate_id} is missing.")
    if str(execution["status"]) in TERMINAL_EXECUTION_STATES:
        raise ApprovalGateError(
            f"Execution {execution_id} is already {execution['status']}; the gate is moot."
        )

    released = repo.release_gate(
        company_id=resolved_company_id,
        gate_id=gate_id,
        status=GATE_COMPLETED,
        decision=decision,
        decided_by=decided_by,
        decision_note=note,
    )
    if not released:
        # Alguém decidiu entre a leitura e a escrita.
        current = repo.get_gate(company_id=resolved_company_id, gate_id=gate_id)
        return {
            "decided": False,
            "reason": "already_decided",
            "gate_id": gate_id,
            "decision": (current or {}).get("decision"),
        }

    log_event(
        logger,
        logging.INFO,
        "automation.gate.decided",
        company_id=resolved_company_id,
        gate_id=gate_id,
        execution_id=execution_id,
        gate_decision=decision,
        actor_id=decided_by,
    )

    deployment = repo.get_deployment(
        company_id=resolved_company_id,
        workflow_id=int(execution["workflow_id"]),
    )
    if deployment is None or str(deployment["state"]) != DEPLOYMENT_ACTIVE:
        # Workflow pausado: a decisão fica registrada, mas nada avança. A
        # execução continua em awaiting_human e retoma quando reativarem.
        log_event(
            logger,
            logging.INFO,
            "automation.gate.decision_held_paused_workflow",
            company_id=resolved_company_id,
            gate_id=gate_id,
            execution_id=execution_id,
        )
        return {
            "decided": True,
            "resumed": False,
            "reason": "workflow_paused",
            "gate_id": gate_id,
            "decision": decision,
        }

    job_id = _enqueue_gate_resume(
        company_id=resolved_company_id,
        execution=execution,
        gate=gate,
        decision=decision,
    )
    if job_id is None:
        return {
            "decided": True,
            "resumed": False,
            "reason": "rejection_path_not_connected",
            "gate_id": gate_id,
            "decision": decision,
        }

    return {
        "decided": True,
        "resumed": True,
        "gate_id": gate_id,
        "decision": decision,
        "job_id": job_id,
    }


def _enqueue_gate_resume(
    *,
    company_id: int,
    execution: dict[str, Any],
    gate: dict[str, Any],
    decision: str,
) -> int | None:
    """Enfileira a retomada pela alça da decisão, na versão original."""
    version = repo.get_version(
        company_id=company_id,
        version_id=int(execution["version_id"]),
    )
    if version is None:
        raise ApprovalGateError("The version pinned by this execution no longer exists.")

    graph = parse_graph(version.get("graph"))
    handle = approval_handle(decision)
    next_node_id = resolve_next(graph, str(gate["node_id"]), handle)
    if next_node_id is None:
        # A validação exige que as duas alças estejam ligadas, mas uma versão
        # antiga pode não estar: fecha a execução em vez de inventar caminho.
        repo.update_execution(
            company_id=company_id,
            execution_id=int(execution["id"]),
            status="succeeded",
            finished=True,
        )
        return None

    job_id = repo.enqueue_job(
        company_id=company_id,
        execution_id=int(execution["id"]),
        job_kind=JOB_KIND_RESUME_GATE,
        node_id=next_node_id,
        max_attempts=settings.automation_max_attempts,
    )
    repo.update_execution(
        company_id=company_id,
        execution_id=int(execution["id"]),
        status=EXECUTION_QUEUED,
        current_node_id=next_node_id,
    )
    return job_id


def resume_held_decisions(*, company_id: int, workflow_id: int) -> dict[str, Any]:
    """Retoma execuções cuja decisão ficou registrada enquanto pausado.

    Chamado ao reativar o workflow. Retoma com a versão original da execução:
    reativar não troca a versão no meio do caminho.
    """
    resolved_company_id = require_company_id(company_id, context="automation gate resume")
    resumed: list[int] = []

    for gate in repo.list_gates(company_id=resolved_company_id, status=GATE_COMPLETED, limit=500):
        execution = repo.get_execution(
            company_id=resolved_company_id,
            execution_id=int(gate["execution_id"]),
        )
        if execution is None:
            continue
        if int(execution["workflow_id"]) != int(workflow_id):
            continue
        if str(execution["status"]) != EXECUTION_AWAITING_HUMAN:
            continue

        job_id = _enqueue_gate_resume(
            company_id=resolved_company_id,
            execution=execution,
            gate=gate,
            decision=str(gate.get("decision") or DECISION_APPROVED),
        )
        if job_id is not None:
            resumed.append(int(execution["id"]))

    if resumed:
        log_event(
            logger,
            logging.INFO,
            "automation.gate.held_decisions_resumed",
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            resumed_count=len(resumed),
        )
    return {"resumed_executions": resumed}
