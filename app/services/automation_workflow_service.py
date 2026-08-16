"""Control plane: workflow, rascunho, validação, simulação e publicação.

Separações que este serviço mantém:

- **rascunho é mutável, versão não é.** Publicar congela a versão; não há
  caminho de escrita para uma versão publicada.
- **publicar não ativa.** Publicar cria a versão e aponta o deployment para
  ela em `paused`. Ativar é uma segunda chamada, com autorização própria.
- **validar é do backend.** O frontend valida para dar feedback; nada
  publica sem passar por `validate_graph` aqui.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.automations.domain import (
    DEPLOYMENT_ACTIVE,
    DEPLOYMENT_PAUSED,
    SCHEMA_VERSION,
    canonical_form,
    content_hash,
    parse_graph,
)
from app.automations.entitlements import CAPABILITY_TO_PLAN_LIMIT
from app.automations.exceptions import (
    GraphValidationError,
    WorkflowConflictError,
    WorkflowNotFoundError,
)
from app.automations.semantics import AiOutput
from app.automations.simulation import simulate
from app.automations.validation import ValidationContext, validate_graph
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.services.billing_service import get_plan_limits_for_company
from app.storage import automation_repository as repo

logger = get_logger(__name__)

_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")

# Gatilhos em que o orquestrador legado já responde de forma síncrona. Um
# envio imediato do fluxo nesses gatilhos produziria mensagem duplicada.
LEGACY_AUTO_REPLY_TRIGGERS = frozenset({"trigger.messageProcessed"})

EMPTY_GRAPH: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "nodes": [], "edges": []}


def build_validation_context(company_id: int) -> ValidationContext:
    """Contexto de validação a partir da configuração real da empresa."""
    resolved_company_id = require_company_id(company_id, context="automation validation")

    # As capabilities vêm do plano contratado. O direito de usar um nó é
    # comercial, não uma flag de configuração da empresa: derivá-lo do plano
    # significa que trocar de plano muda o que é publicável imediatamente, e
    # que não existe um segundo lugar onde alguém possa liberar um nó que o
    # contrato não cobre.
    plan_limits = get_plan_limits_for_company(resolved_company_id)
    capabilities = {
        capability
        for capability, plan_limit in CAPABILITY_TO_PLAN_LIMIT.items()
        if int(plan_limits.get(plan_limit, 0) or 0) > 0
    }

    return ValidationContext(
        company_id=resolved_company_id,
        capabilities=frozenset(capabilities),
        legacy_auto_reply_triggers=LEGACY_AUTO_REPLY_TRIGGERS,
    )


def _require_workflow(company_id: int, workflow_id: int) -> dict[str, Any]:
    workflow = repo.get_workflow(company_id=company_id, workflow_id=workflow_id)
    if workflow is None:
        # Mesma exceção para inexistente e de outra empresa: diferenciar
        # permitiria enumerar workflows de outros tenants.
        raise WorkflowNotFoundError(f"Workflow {workflow_id} not found.")
    return workflow


def create_workflow(
    *,
    company_id: int,
    workflow_key: str,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="automation workflow create")
    normalized_key = str(workflow_key or "").strip().lower()
    if not _KEY_PATTERN.match(normalized_key):
        raise ValueError(
            "workflow_key must be lowercase alphanumeric with hyphens, 2-64 characters."
        )
    if repo.get_workflow_by_key(company_id=resolved_company_id, workflow_key=normalized_key):
        raise ValueError(f"Workflow '{normalized_key}' already exists in this company.")

    workflow_id = repo.create_workflow(
        company_id=resolved_company_id,
        workflow_key=normalized_key,
        name=str(name or normalized_key).strip(),
        description=description,
        created_by=created_by,
    )
    repo.create_draft(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        schema_version=SCHEMA_VERSION,
        graph=EMPTY_GRAPH,
        updated_by=created_by,
    )
    log_event(
        logger,
        logging.INFO,
        "automation.workflow.created",
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        workflow_key=normalized_key,
    )
    return describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id)


def describe_workflow(*, company_id: int, workflow_id: int) -> dict[str, Any]:
    """Workflow com rascunho, deployment e a versão publicada corrente."""
    resolved_company_id = require_company_id(company_id, context="automation workflow read")
    workflow = _require_workflow(resolved_company_id, workflow_id)
    draft = repo.get_draft(company_id=resolved_company_id, workflow_id=workflow_id)
    deployment = repo.get_deployment(company_id=resolved_company_id, workflow_id=workflow_id)
    version = (
        repo.get_version(company_id=resolved_company_id, version_id=int(deployment["version_id"]))
        if deployment
        else None
    )

    draft_hash = content_hash(parse_graph(draft.get("graph") if draft else None))
    published_hash = str(version.get("content_hash")) if version else None

    return {
        **workflow,
        "archived": bool(int(workflow.get("archived") or 0)),
        "draft": draft,
        "deployment": deployment,
        "deployed_version": version,
        "draft_matches_published": bool(published_hash) and draft_hash == published_hash,
        "has_unpublished_changes": draft is not None and draft_hash != published_hash,
    }


def list_workflows(
    *,
    company_id: int,
    include_archived: bool = False,
    search: str | None = None,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="automation workflow listing")
    workflows = repo.list_workflows(
        company_id=resolved_company_id,
        include_archived=include_archived,
        search=search,
    )
    summaries: list[dict[str, Any]] = []
    for workflow in workflows:
        deployment = repo.get_deployment(
            company_id=resolved_company_id,
            workflow_id=int(workflow["id"]),
        )
        summaries.append(
            {
                **workflow,
                "archived": bool(int(workflow.get("archived") or 0)),
                "state": str((deployment or {}).get("state") or "draft"),
                "deployed_version_id": (deployment or {}).get("version_id"),
                "trigger_type": (deployment or {}).get("trigger_type"),
            }
        )
    return summaries


def save_draft(
    *,
    company_id: int,
    workflow_id: int,
    graph: dict[str, Any],
    expected_revision: int,
    viewport: dict[str, Any] | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Grava o rascunho com concorrência otimista.

    Revisão desatualizada levanta `WorkflowConflictError`, que a rota traduz
    em 409. Last-write-wins apagaria o trabalho do outro editor sem aviso.
    """
    resolved_company_id = require_company_id(company_id, context="automation draft write")
    _require_workflow(resolved_company_id, workflow_id)

    current = repo.get_draft(company_id=resolved_company_id, workflow_id=workflow_id)
    if current is None:
        repo.create_draft(
            company_id=resolved_company_id,
            workflow_id=workflow_id,
            schema_version=str(graph.get("schema_version") or SCHEMA_VERSION),
            graph=graph,
            viewport=viewport,
            updated_by=updated_by,
        )
        return describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id)

    new_revision = repo.save_draft_if_current(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        expected_revision=int(expected_revision),
        schema_version=str(graph.get("schema_version") or SCHEMA_VERSION),
        graph=graph,
        viewport=viewport,
        updated_by=updated_by,
    )
    if new_revision is None:
        raise WorkflowConflictError(int(expected_revision), int(current["revision"]))

    repo.update_workflow(company_id=resolved_company_id, workflow_id=workflow_id)
    return describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id)


def validate_workflow_graph(
    *,
    company_id: int,
    graph: dict[str, Any],
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="automation validation")
    result = validate_graph(
        parse_graph(graph),
        context=build_validation_context(resolved_company_id),
    )
    return result.as_dict()


def simulate_workflow_graph(
    *,
    company_id: int,
    graph: dict[str, Any],
    sample_context: dict[str, Any] | None = None,
    approval_decisions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Simula sem produzir efeito.

    A simulação roda mesmo com o grafo inválido — é justamente enquanto ele
    está incompleto que ver o caminho ajuda. A validação vai junto na
    resposta para que a UI mostre as duas coisas.
    """
    resolved_company_id = require_company_id(company_id, context="automation simulation")
    parsed = parse_graph(graph)
    validation = validate_graph(parsed, context=build_validation_context(resolved_company_id))

    result = simulate(
        parsed,
        context=dict(sample_context or {}),
        approval_decisions=approval_decisions,
        # Saída de IA fictícia e marcada como tal: a simulação não chama
        # provider nenhum.
        ai_outputs={
            node.id: AiOutput(
                text="[simulação] mensagem que a IA produziria aqui.",
                label=str((sample_context or {}).get("classification") or ""),
                simulated=True,
            )
            for node in parsed.nodes
        },
    )
    return {"validation": validation.as_dict(), "simulation": result.as_dict()}


def publish_workflow(
    *,
    company_id: int,
    workflow_id: int,
    notes: str | None = None,
    published_by: str | None = None,
) -> dict[str, Any]:
    """Publica o rascunho como versão imutável, sempre pausada.

    Um grafo inválido nunca chega a criar versão: `GraphValidationError` sai
    antes de qualquer escrita.
    """
    resolved_company_id = require_company_id(company_id, context="automation publish")
    _require_workflow(resolved_company_id, workflow_id)

    draft = repo.get_draft(company_id=resolved_company_id, workflow_id=workflow_id)
    if draft is None:
        raise GraphValidationError(
            [{"code": "missing_draft", "message": "O fluxo não tem rascunho para publicar."}]
        )

    parsed = parse_graph(draft.get("graph"))
    result = validate_graph(parsed, context=build_validation_context(resolved_company_id))
    if not result.is_valid:
        raise GraphValidationError([issue.as_dict() for issue in result.errors])
    if result.trigger_type is None:
        raise GraphValidationError(
            [{"code": "missing_trigger", "message": "O fluxo precisa de exatamente um gatilho."}]
        )

    version_number = repo.get_next_version_number(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
    )
    version_id = repo.create_version(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        version=version_number,
        schema_version=parsed.schema_version,
        graph=parsed.as_dict(),
        canonical_form=canonical_form(parsed),
        content_hash=content_hash(parsed),
        trigger_type=result.trigger_type,
        published_by=published_by,
        notes=notes,
    )
    # Publicar sempre cria pausado. Ativar é ação separada.
    repo.upsert_deployment(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        version_id=version_id,
        trigger_type=result.trigger_type,
        state=DEPLOYMENT_PAUSED,
        created_by=published_by,
    )
    log_event(
        logger,
        logging.INFO,
        "automation.workflow.published",
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        version_id=version_id,
        automation_version=version_number,
        trigger_type=result.trigger_type,
    )
    return {
        "workflow_id": workflow_id,
        "version_id": version_id,
        "version": version_number,
        "trigger_type": result.trigger_type,
        "state": DEPLOYMENT_PAUSED,
        "warnings": [issue.as_dict() for issue in result.warnings],
    }


def set_workflow_state(
    *,
    company_id: int,
    workflow_id: int,
    state: str,
    actor: str | None = None,
) -> dict[str, Any]:
    """Ativa ou pausa o deployment.

    Pausar bloqueia novas entradas e **preserva** execuções em espera: elas
    retomam com a versão original quando o wait vencer.
    """
    resolved_company_id = require_company_id(company_id, context="automation deployment state")
    _require_workflow(resolved_company_id, workflow_id)
    if state not in {DEPLOYMENT_ACTIVE, DEPLOYMENT_PAUSED}:
        raise ValueError(f"Invalid deployment state: {state}")

    deployment = repo.get_deployment(company_id=resolved_company_id, workflow_id=workflow_id)
    if deployment is None:
        raise GraphValidationError(
            [
                {
                    "code": "not_published",
                    "message": "Publique uma versão antes de ativar o fluxo.",
                }
            ]
        )

    if state == DEPLOYMENT_ACTIVE:
        # Revalida a versão que vai ao ar: uma versão publicada sob regra
        # anterior não é ativada por engano.
        version = repo.get_version(
            company_id=resolved_company_id,
            version_id=int(deployment["version_id"]),
        )
        if version is None:
            raise GraphValidationError(
                [{"code": "missing_version", "message": "A versão do deployment não existe."}]
            )
        result = validate_graph(
            parse_graph(version.get("graph")),
            context=build_validation_context(resolved_company_id),
        )
        if not result.is_valid:
            raise GraphValidationError([issue.as_dict() for issue in result.errors])

    repo.set_deployment_state(
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        state=state,
        actor=actor,
    )

    resumed: list[int] = []
    if state == DEPLOYMENT_ACTIVE:
        # Decisões humanas tomadas enquanto o fluxo estava pausado ficaram
        # registradas sem avançar. Reativar as retoma — com a versão original
        # de cada execução, não com a versão recém-ativada.
        from app.services.automation_approval_service import (
            resume_held_decisions,
        )

        resumed = list(
            resume_held_decisions(
                company_id=resolved_company_id,
                workflow_id=workflow_id,
            )["resumed_executions"]
        )

    log_event(
        logger,
        logging.INFO,
        "automation.workflow.state_changed",
        company_id=resolved_company_id,
        workflow_id=workflow_id,
        deployment_state=state,
        actor_id=actor,
        resumed_executions=len(resumed),
    )
    return {
        **describe_workflow(company_id=resolved_company_id, workflow_id=workflow_id),
        "resumed_executions": resumed,
    }


def diff_draft_against_published(*, company_id: int, workflow_id: int) -> dict[str, Any]:
    """Diferença entre o rascunho e a versão publicada.

    Compara comportamento, não posição: mover um bloco no canvas não aparece
    como mudança porque não muda o que o fluxo faz.
    """
    resolved_company_id = require_company_id(company_id, context="automation diff")
    _require_workflow(resolved_company_id, workflow_id)

    draft = repo.get_draft(company_id=resolved_company_id, workflow_id=workflow_id)
    deployment = repo.get_deployment(company_id=resolved_company_id, workflow_id=workflow_id)
    published = (
        repo.get_version(company_id=resolved_company_id, version_id=int(deployment["version_id"]))
        if deployment
        else None
    )

    draft_graph = parse_graph(draft.get("graph") if draft else None)
    published_graph = parse_graph(published.get("graph") if published else None)

    draft_nodes = {node.id: node for node in draft_graph.nodes}
    published_nodes = {node.id: node for node in published_graph.nodes}

    added = sorted(set(draft_nodes) - set(published_nodes))
    removed = sorted(set(published_nodes) - set(draft_nodes))
    changed = sorted(
        node_id
        for node_id in set(draft_nodes) & set(published_nodes)
        if (
            draft_nodes[node_id].node_type != published_nodes[node_id].node_type
            or draft_nodes[node_id].config != published_nodes[node_id].config
        )
    )

    draft_edges = {(edge.source, edge.source_handle, edge.target) for edge in draft_graph.edges}
    published_edges = {
        (edge.source, edge.source_handle, edge.target) for edge in published_graph.edges
    }

    return {
        "has_published_version": published is not None,
        "published_version": (published or {}).get("version"),
        "identical": content_hash(draft_graph) == content_hash(published_graph),
        "nodes_added": added,
        "nodes_removed": removed,
        "nodes_changed": changed,
        "edges_added": sorted(
            f"{source}:{handle or 'next'}->{target}"
            for source, handle, target in draft_edges - published_edges
        ),
        "edges_removed": sorted(
            f"{source}:{handle or 'next'}->{target}"
            for source, handle, target in published_edges - draft_edges
        ),
    }
