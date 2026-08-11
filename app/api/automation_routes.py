"""Rotas do Automation Studio.

Todas tenant-scoped. A empresa vem sempre de `require_authorized_company_id`,
que lê o escopo já autorizado por `require_company_scope` — nenhum handler
aqui resolve empresa por conta própria.

Papéis:
- **viewer**: lê workflows, execuções redigidas e a fila de aprovações;
- **operator**: dispara manualmente, faz retry/cancel e decide portões;
- **superadmin** (dentro das memberships): cria, edita, publica e ativa.

Separação que as rotas preservam: publicar (`/publish`) cria a versão pausada;
ativar (`/state`) é uma chamada distinta. Não há rota que faça as duas.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.admin_routes import (
    _record_audit_event,
    require_admin_access,
    require_admin_role,
    require_company_scope,
)
from app.api.admin_security import get_request_principal
from app.api.automation_schemas import (
    DraftSavePayload,
    GateDecisionPayload,
    GraphSimulationPayload,
    GraphValidationPayload,
    ManualTriggerPayload,
    WorkflowCreatePayload,
    WorkflowPublishPayload,
    WorkflowStatePayload,
    WorkflowUpdatePayload,
)
from app.api.company_scope import require_authorized_company_id
from app.automations.catalog import describe_catalog
from app.automations.exceptions import (
    ApprovalGateError,
    ExecutionNotFoundError,
    GraphValidationError,
    WorkflowConflictError,
    WorkflowNotFoundError,
)
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.services import automation_approval_service as approvals
from app.services import automation_execution_service as executions
from app.services import automation_workflow_service as workflows
from app.services.automation_trigger_service import emit_manual_trigger
from app.storage import automation_repository as repo

logger = get_logger(__name__)

automation_router = APIRouter(
    prefix="/admin/automations",
    tags=["automations"],
    dependencies=[Depends(require_admin_access), Depends(require_company_scope)],
)


def _actor(request: Request) -> str:
    """Identidade real do principal, nunca o rótulo do header."""
    return get_request_principal(request).actor


def _require_engine_enabled() -> None:
    if not settings.automation_engine_enabled:
        raise HTTPException(
            status_code=409,
            detail=(
                "Automation engine is disabled. Set AUTOMATION_ENGINE_ENABLED=true "
                "to operate automations."
            ),
        )


def _not_found(exc: Exception) -> HTTPException:
    # Inexistente e de outra empresa respondem igual: diferenciar permitiria
    # enumerar recursos de outros tenants.
    return HTTPException(status_code=404, detail=str(exc))


# --- catálogo -------------------------------------------------------------------------


@automation_router.get("/catalog")
def read_catalog() -> dict[str, Any]:
    """Catálogo de nós. Fonte única do inspector e do validador."""
    return {
        "schema_version": "automation-graph/1",
        "nodes": describe_catalog(),
        "engine_enabled": settings.automation_engine_enabled,
        "worker_enabled": settings.automation_worker_enabled,
        "limits": {
            "max_steps_per_execution": settings.automation_max_steps_per_execution,
            "max_attempts": settings.automation_max_attempts,
            "max_event_bytes": settings.automation_max_event_bytes,
        },
    }


# --- workflows ------------------------------------------------------------------------


@automation_router.get("/workflows")
def list_workflows(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=120),
) -> list[dict[str, Any]]:
    return workflows.list_workflows(
        company_id=require_authorized_company_id(request),
        include_archived=include_archived,
        search=search,
    )


@automation_router.post(
    "/workflows",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def create_workflow(
    request: Request,
    payload: WorkflowCreatePayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = require_authorized_company_id(request)
    try:
        created = workflows.create_workflow(
            company_id=resolved_company_id,
            workflow_key=payload.workflow_key,
            name=payload.name,
            description=payload.description,
            created_by=_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="create",
        resource_type="automation_workflow",
        resource_id=str(created["id"]),
        company_id=resolved_company_id,
        metadata={"workflow_key": payload.workflow_key},
    )
    return created


@automation_router.get("/workflows/{workflow_id}")
def read_workflow(
    request: Request,
    workflow_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return workflows.describe_workflow(
            company_id=require_authorized_company_id(request),
            workflow_id=workflow_id,
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc


@automation_router.patch(
    "/workflows/{workflow_id}",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def update_workflow(
    request: Request,
    workflow_id: int,
    payload: WorkflowUpdatePayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = require_authorized_company_id(request)
    try:
        workflows.describe_workflow(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc

    repo.update_workflow(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        name=payload.name,
        description=payload.description,
        archived=payload.archived,
    )
    _record_audit_event(
        request=request,
        action="update",
        resource_type="automation_workflow",
        resource_id=str(workflow_id),
        company_id=resolved_company_id,
        metadata={"archived": payload.archived},
    )
    return workflows.describe_workflow(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
    )


# --- rascunho, validação e simulação -------------------------------------------------


@automation_router.put(
    "/workflows/{workflow_id}/draft",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def save_draft(
    request: Request,
    workflow_id: int,
    payload: DraftSavePayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Salva o rascunho com concorrência otimista.

    Revisão desatualizada responde 409 com a revisão corrente, para que o
    editor possa recarregar em vez de sobrescrever o trabalho do outro.
    """
    resolved_company_id = require_authorized_company_id(request)
    try:
        saved = workflows.save_draft(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            graph=payload.graph,
            expected_revision=payload.expected_revision,
            viewport=payload.viewport,
            updated_by=_actor(request),
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    except WorkflowConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.actual_revision,
            },
        ) from exc

    _record_audit_event(
        request=request,
        action="update",
        resource_type="automation_workflow_draft",
        resource_id=str(workflow_id),
        company_id=resolved_company_id,
        metadata={"revision": (saved.get("draft") or {}).get("revision")},
    )
    return saved


@automation_router.post("/workflows/validate")
def validate_graph(
    request: Request,
    payload: GraphValidationPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Validação autoritativa. O frontend valida só para UX."""
    return workflows.validate_workflow_graph(
        company_id=require_authorized_company_id(request),
        graph=payload.graph,
    )


@automation_router.post("/workflows/simulate")
def simulate_graph(
    request: Request,
    payload: GraphSimulationPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Simula sem produzir efeito. Nenhum provider, canal ou banco é tocado."""
    return workflows.simulate_workflow_graph(
        company_id=require_authorized_company_id(request),
        graph=payload.graph,
        sample_context=payload.sample_context,
        approval_decisions=payload.approval_decisions,
    )


@automation_router.get("/workflows/{workflow_id}/diff")
def read_diff(
    request: Request,
    workflow_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return workflows.diff_draft_against_published(
            company_id=require_authorized_company_id(request),
            workflow_id=workflow_id,
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc


# --- publicação e estado ---------------------------------------------------------------


@automation_router.post(
    "/workflows/{workflow_id}/publish",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def publish_workflow(
    request: Request,
    workflow_id: int,
    payload: WorkflowPublishPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Publica uma versão imutável, **pausada**. Ativar é outra chamada."""
    resolved_company_id = require_authorized_company_id(request)
    try:
        published = workflows.publish_workflow(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            notes=payload.notes,
            published_by=_actor(request),
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    except GraphValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "O fluxo tem erros e não pode ser publicado.", "issues": exc.issues},
        ) from exc

    _record_audit_event(
        request=request,
        action="publish",
        resource_type="automation_workflow_version",
        resource_id=str(published["version_id"]),
        company_id=resolved_company_id,
        metadata={
            "workflow_id": workflow_id,
            "version": published["version"],
            "trigger_type": published["trigger_type"],
            "state": published["state"],
        },
    )
    return published


@automation_router.get("/workflows/{workflow_id}/versions")
def list_versions(
    request: Request,
    workflow_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    resolved_company_id = require_authorized_company_id(request)
    try:
        workflows.describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    return repo.list_versions(company_id=resolved_company_id, workflow_id=workflow_id)


@automation_router.post(
    "/workflows/{workflow_id}/state",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def set_workflow_state(
    request: Request,
    workflow_id: int,
    payload: WorkflowStatePayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Ativa ou pausa. Pausar preserva execuções já em espera."""
    _require_engine_enabled()
    resolved_company_id = require_authorized_company_id(request)
    try:
        result = workflows.set_workflow_state(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            state=payload.state,
            actor=_actor(request),
        )
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    except GraphValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "O fluxo não pode ser ativado.", "issues": exc.issues},
        ) from exc

    _record_audit_event(
        request=request,
        action="activate" if payload.state == "active" else "pause",
        resource_type="automation_deployment",
        resource_id=str(workflow_id),
        company_id=resolved_company_id,
        metadata={
            "state": payload.state,
            "resumed_executions": len(result.get("resumed_executions") or []),
        },
    )
    return result


@automation_router.post(
    "/workflows/{workflow_id}/trigger",
    dependencies=[Depends(require_admin_role("operator"))],
)
def trigger_workflow(
    request: Request,
    workflow_id: int,
    payload: ManualTriggerPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Disparo manual, idempotente pela `idempotency_key`."""
    _require_engine_enabled()
    resolved_company_id = require_authorized_company_id(request)
    try:
        workflows.describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc

    result = emit_manual_trigger(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        triggered_by=_actor(request),
        contact_id=payload.contact_id,
        nonce=payload.idempotency_key,
    )
    _record_audit_event(
        request=request,
        action="trigger",
        resource_type="automation_workflow",
        resource_id=str(workflow_id),
        company_id=resolved_company_id,
        metadata={"emitted": result.get("emitted"), "created": result.get("created")},
    )
    return result


# --- execuções -------------------------------------------------------------------------


@automation_router.get("/executions")
def list_executions(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    workflow_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return executions.list_executions(
        company_id=require_authorized_company_id(request),
        workflow_id=workflow_id,
        status=status,
        limit=limit,
    )


@automation_router.get("/executions/{execution_id}")
def read_execution(
    request: Request,
    execution_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return executions.describe_execution(
            company_id=require_authorized_company_id(request),
            execution_id=execution_id,
        )
    except ExecutionNotFoundError as exc:
        raise _not_found(exc) from exc


@automation_router.post(
    "/executions/{execution_id}/retry",
    dependencies=[Depends(require_admin_role("operator"))],
)
def retry_execution(
    request: Request,
    execution_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Retry manual. Efeito já concluído não é repetido — o ledger decide."""
    _require_engine_enabled()
    resolved_company_id = require_authorized_company_id(request)
    try:
        result = executions.retry_execution(
            company_id=resolved_company_id,
            execution_id=execution_id,
            actor=_actor(request),
        )
    except ExecutionNotFoundError as exc:
        raise _not_found(exc) from exc

    _record_audit_event(
        request=request,
        action="retry",
        resource_type="automation_execution",
        resource_id=str(execution_id),
        company_id=resolved_company_id,
        metadata={"retried": result.get("retried"), "reason": result.get("reason")},
    )
    return result


@automation_router.post(
    "/executions/{execution_id}/cancel",
    dependencies=[Depends(require_admin_role("operator"))],
)
def cancel_execution(
    request: Request,
    execution_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_company_id = require_authorized_company_id(request)
    try:
        result = executions.cancel_execution(
            company_id=resolved_company_id,
            execution_id=execution_id,
            actor=_actor(request),
        )
    except ExecutionNotFoundError as exc:
        raise _not_found(exc) from exc

    _record_audit_event(
        request=request,
        action="cancel",
        resource_type="automation_execution",
        resource_id=str(execution_id),
        company_id=resolved_company_id,
        metadata={"canceled": result.get("canceled"), "reason": result.get("reason")},
    )
    return result


@automation_router.get("/dead-letter")
def read_dead_letter(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return executions.list_dead_letter(
        company_id=require_authorized_company_id(request),
        limit=limit,
    )


# --- portões humanos -------------------------------------------------------------------


@automation_router.get("/approvals")
def list_approvals(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return approvals.list_pending_gates(
        company_id=require_authorized_company_id(request),
        limit=limit,
    )


@automation_router.post(
    "/approvals/{gate_id}/claim",
    dependencies=[Depends(require_admin_role("operator"))],
)
def claim_approval(
    request: Request,
    gate_id: int,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Reserva o portão. Se outro operador chegou antes, responde 409."""
    resolved_company_id = require_authorized_company_id(request)
    try:
        gate = approvals.claim_gate(
            company_id=resolved_company_id,
            gate_id=gate_id,
            claimed_by=_actor(request),
        )
    except ApprovalGateError as exc:
        if "not found" in str(exc):
            raise _not_found(exc) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _record_audit_event(
        request=request,
        action="claim",
        resource_type="automation_approval_gate",
        resource_id=str(gate_id),
        company_id=resolved_company_id,
    )
    return gate


@automation_router.post(
    "/approvals/{gate_id}/decide",
    dependencies=[Depends(require_admin_role("operator"))],
)
def decide_approval(
    request: Request,
    gate_id: int,
    payload: GateDecisionPayload,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> dict[str, Any]:
    """Decide o portão. Idempotente e auditado com o principal real."""
    resolved_company_id = require_authorized_company_id(request)
    try:
        result = approvals.decide_gate(
            company_id=resolved_company_id,
            gate_id=gate_id,
            decision=payload.decision,
            decided_by=_actor(request),
            note=payload.note,
        )
    except ApprovalGateError as exc:
        if "not found" in str(exc):
            raise _not_found(exc) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    log_event(
        logger,
        logging.INFO,
        "admin.automation.gate_decided",
        company_id=resolved_company_id,
        gate_id=gate_id,
        gate_decision=payload.decision,
        actor_id=_actor(request),
    )
    _record_audit_event(
        request=request,
        action="decide",
        resource_type="automation_approval_gate",
        resource_id=str(gate_id),
        company_id=resolved_company_id,
        metadata={
            "decision": payload.decision,
            "resumed": result.get("resumed"),
            "reason": result.get("reason"),
        },
    )
    return result


# --- operação do worker (plataforma) ----------------------------------------------------


@automation_router.post(
    "/worker/run-once",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def run_worker_once(request: Request) -> dict[str, Any]:
    """Roda um ciclo do worker. Operação de plataforma, não de tenant.

    Existe para operação manual e para o runbook. A política central já exige
    superadmin **global** neste caminho, então nenhum tenant dispara o worker
    de outro.
    """
    _require_engine_enabled()
    from app.tools.automation_worker import run_worker_iteration

    result = run_worker_iteration()
    _record_audit_event(
        request=request,
        action="run",
        resource_type="automation_worker",
        metadata={
            "processed": result["executed"]["processed"],
            "dispatched": result["dispatched"]["dispatched"],
        },
    )
    return result
