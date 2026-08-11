"""Migrations 048–053 — Automation Studio (control plane e data plane).

Aditivas, idempotentes e compatíveis com SQLite e Postgres. Não alteram
nenhuma migration já aplicada.

A separação em duas famílias de tabelas é deliberada:

- **control plane** (048, 049): workflow, rascunho, versão imutável e
  deployment. Muda quando um humano edita ou publica.
- **data plane** (050–053): evento, outbox, execução, step, job, efeito e
  portão humano. Muda a cada mensagem processada.

Toda tabela tenant-scoped carrega `company_id` e o índice principal começa
por ele: o isolamento acontece dentro da query, nunca em memória.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import create_index, create_table, create_unique_index

Migration = tuple[str, str, Callable[[Any], None]]

AUTOMATION_ID_TABLES = {
    "automation_workflows",
    "automation_workflow_drafts",
    "automation_workflow_versions",
    "automation_deployments",
    "automation_events",
    "automation_outbox",
    "automation_executions",
    "automation_step_executions",
    "automation_jobs",
    "automation_effects",
    "automation_approval_gates",
}


def _create_workflow_control_plane(cursor: Any) -> None:
    """Workflow, rascunho editável e versão imutável.

    `automation_workflow_drafts` guarda `revision` para concorrência
    otimista: dois editores simultâneos produzem 409, nunca last-write-wins.
    """
    create_table(
        cursor,
        "automation_workflows",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            workflow_key TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_workflow_drafts",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            workflow_id INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            schema_version TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            viewport_json TEXT,
            updated_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_workflow_versions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            workflow_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            schema_version TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            canonical_form TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            published_by TEXT,
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        """,
    )

    create_unique_index(
        cursor,
        "idx_automation_workflows_company_key",
        "automation_workflows",
        "company_id, workflow_key",
    )
    create_index(
        cursor,
        "idx_automation_workflows_company",
        "automation_workflows",
        "company_id, archived, updated_at",
    )
    create_unique_index(
        cursor,
        "idx_automation_drafts_workflow",
        "automation_workflow_drafts",
        "company_id, workflow_id",
    )
    create_unique_index(
        cursor,
        "idx_automation_versions_workflow_version",
        "automation_workflow_versions",
        "company_id, workflow_id, version",
    )
    create_index(
        cursor,
        "idx_automation_versions_trigger",
        "automation_workflow_versions",
        "company_id, trigger_type",
    )


def _create_deployments(cursor: Any) -> None:
    """Deployment aponta para uma versão exata e nunca para "a mais recente".

    Publicar cria o deployment em `paused`. Ativar é uma segunda ação, com
    autorização própria — nenhuma empresa passa a executar automação por
    efeito colateral de uma publicação.
    """
    create_table(
        cursor,
        "automation_deployments",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            workflow_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            trigger_type TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'paused',
            activated_by TEXT,
            activated_at TIMESTAMP,
            paused_by TEXT,
            paused_at TIMESTAMP,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    # Um workflow tem no máximo um deployment corrente. Trocar de versão
    # substitui a linha; o histórico de versões vive em workflow_versions.
    create_unique_index(
        cursor,
        "idx_automation_deployments_workflow",
        "automation_deployments",
        "company_id, workflow_id",
    )
    create_index(
        cursor,
        "idx_automation_deployments_active_trigger",
        "automation_deployments",
        "company_id, state, trigger_type",
    )


def _create_event_plane(cursor: Any) -> None:
    """Evento canônico e outbox transacional.

    `event_key` é único dentro de `company_id`: reentregar o mesmo fato não
    cria um segundo evento nem uma segunda execução.
    """
    create_table(
        cursor,
        "automation_events",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            event_type TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_bytes INTEGER NOT NULL DEFAULT 0,
            occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_outbox",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            available_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            lease_owner TEXT,
            lease_expires_at TIMESTAMP,
            dispatched_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )

    create_unique_index(
        cursor,
        "idx_automation_events_company_key",
        "automation_events",
        "company_id, event_key",
    )
    create_index(
        cursor,
        "idx_automation_events_type",
        "automation_events",
        "company_id, event_type, occurred_at",
    )
    create_unique_index(
        cursor,
        "idx_automation_outbox_event",
        "automation_outbox",
        "company_id, event_id",
    )
    create_index(
        cursor,
        "idx_automation_outbox_claimable",
        "automation_outbox",
        "status, available_at, id",
    )


def _create_execution_plane(cursor: Any) -> None:
    """Execução, step e job durável.

    A execução guarda `version_id`: ela permanece presa à versão com que
    começou mesmo que o workflow seja republicado no meio.
    """
    create_table(
        cursor,
        "automation_executions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            workflow_id INTEGER NOT NULL,
            version_id INTEGER NOT NULL,
            event_id INTEGER,
            enrollment_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            current_node_id TEXT,
            steps_executed INTEGER NOT NULL DEFAULT 0,
            context_json TEXT NOT NULL DEFAULT '{}',
            failure_reason TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_step_executions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            execution_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            node_id TEXT NOT NULL,
            node_type TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            chosen_handle TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            failure_reason TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_jobs",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            execution_id INTEGER NOT NULL,
            job_kind TEXT NOT NULL,
            node_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            lease_owner TEXT,
            lease_expires_at TIMESTAMP,
            last_error TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )

    create_unique_index(
        cursor,
        "idx_automation_executions_enrollment",
        "automation_executions",
        "company_id, workflow_id, enrollment_key",
    )
    create_index(
        cursor,
        "idx_automation_executions_status",
        "automation_executions",
        "company_id, status, updated_at",
    )
    create_index(
        cursor,
        "idx_automation_executions_workflow",
        "automation_executions",
        "company_id, workflow_id, started_at",
    )
    create_unique_index(
        cursor,
        "idx_automation_steps_sequence",
        "automation_step_executions",
        "company_id, execution_id, sequence",
    )
    create_index(
        cursor,
        "idx_automation_steps_execution",
        "automation_step_executions",
        "company_id, execution_id, id",
    )
    # O claim varre por status/run_at sem filtrar empresa: o worker é global e
    # o isolamento acontece depois, ao carregar a execução.
    create_index(
        cursor,
        "idx_automation_jobs_claimable",
        "automation_jobs",
        "status, run_at, id",
    )
    create_index(
        cursor,
        "idx_automation_jobs_execution",
        "automation_jobs",
        "company_id, execution_id, status",
    )
    create_index(
        cursor,
        "idx_automation_jobs_lease",
        "automation_jobs",
        "status, lease_expires_at",
    )


def _create_effect_and_approval_plane(cursor: Any) -> None:
    """Ledger de efeitos externos e portões humanos.

    `effect_key` é único por empresa. Um retry que reexecute o mesmo nó
    lógico encontra o efeito já concluído e não dispara a ação de novo.
    """
    create_table(
        cursor,
        "automation_effects",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            execution_id INTEGER NOT NULL,
            effect_key TEXT NOT NULL,
            effect_kind TEXT NOT NULL,
            node_id TEXT NOT NULL,
            occurrence INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            request_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            failure_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "automation_approval_gates",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            execution_id INTEGER NOT NULL,
            node_id TEXT NOT NULL,
            occurrence INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'awaiting_human',
            title TEXT NOT NULL,
            summary TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            claimed_by TEXT,
            claimed_at TIMESTAMP,
            lease_expires_at TIMESTAMP,
            decided_by TEXT,
            decided_at TIMESTAMP,
            decision TEXT,
            decision_note TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )

    create_unique_index(
        cursor,
        "idx_automation_effects_key",
        "automation_effects",
        "company_id, effect_key",
    )
    create_index(
        cursor,
        "idx_automation_effects_execution",
        "automation_effects",
        "company_id, execution_id, status",
    )
    create_unique_index(
        cursor,
        "idx_automation_gates_occurrence",
        "automation_approval_gates",
        "company_id, execution_id, node_id, occurrence",
    )
    create_index(
        cursor,
        "idx_automation_gates_pending",
        "automation_approval_gates",
        "company_id, status, created_at",
    )
    create_index(
        cursor,
        "idx_automation_gates_lease",
        "automation_approval_gates",
        "status, lease_expires_at",
    )


def _create_dead_letter_index(cursor: Any) -> None:
    """Dead-letter consultável sem varrer a tabela inteira."""
    create_index(
        cursor,
        "idx_automation_jobs_dead_letter",
        "automation_jobs",
        "company_id, status, updated_at",
    )
    create_index(
        cursor,
        "idx_automation_outbox_dead_letter",
        "automation_outbox",
        "company_id, status, updated_at",
    )


AUTOMATION_MIGRATIONS: list[Migration] = [
    (
        "048_automation_control_plane",
        "Create automation workflows, drafts and immutable versions",
        _create_workflow_control_plane,
    ),
    (
        "049_automation_deployments",
        "Create automation deployments pinned to an exact version",
        _create_deployments,
    ),
    (
        "050_automation_event_plane",
        "Create canonical automation events and the transactional outbox",
        _create_event_plane,
    ),
    (
        "051_automation_execution_plane",
        "Create automation executions, step executions and durable jobs",
        _create_execution_plane,
    ),
    (
        "052_automation_effects_and_gates",
        "Create the automation effect ledger and human approval gates",
        _create_effect_and_approval_plane,
    ),
    (
        "053_automation_dead_letter_indexes",
        "Index the automation dead-letter queues for inspection",
        _create_dead_letter_index,
    ),
]
