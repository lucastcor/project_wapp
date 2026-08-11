"""Migrations 038+ — identidade, autorização por tenant e proveniência.

Segunda rodada da plataforma multiempresa. Todas aditivas, idempotentes e
compatíveis com SQLite e Postgres. Não alteram as migrations 024–037.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import (
    create_index,
    create_table,
    create_unique_index,
    ensure_column,
)

Migration = tuple[str, str, Callable[[Any], None]]

IDENTITY_ID_TABLES = {
    "platform_principals",
    "platform_access_tokens",
    "platform_company_memberships",
    "tenant_api_credentials",
}


def _create_platform_identity(cursor: Any) -> None:
    """Principal administrativo, credenciais e vínculo com empresas.

    O papel global deixa de ser suficiente: `operator` e `viewer` só alcançam
    as empresas presentes em `platform_company_memberships`.
    """
    create_table(
        cursor,
        "platform_principals",
        """
            id ID_PK,
            principal_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            global_role TEXT NOT NULL DEFAULT 'viewer',
            is_global INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "platform_access_tokens",
        """
            id ID_PK,
            principal_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            token_prefix TEXT NOT NULL,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            revoked_at TIMESTAMP,
            last_used_at TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "platform_company_memberships",
        """
            id ID_PK,
            principal_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )

    create_unique_index(
        cursor, "idx_platform_principals_key", "platform_principals", "principal_key"
    )
    create_unique_index(
        cursor, "idx_platform_access_tokens_hash", "platform_access_tokens", "token_hash"
    )
    create_index(
        cursor,
        "idx_platform_access_tokens_principal",
        "platform_access_tokens",
        "principal_id, active",
    )
    create_unique_index(
        cursor,
        "idx_platform_memberships_principal_company",
        "platform_company_memberships",
        "principal_id, company_id",
    )
    create_index(
        cursor,
        "idx_platform_memberships_company",
        "platform_company_memberships",
        "company_id, role",
    )


def _create_tenant_api_credentials(cursor: Any) -> None:
    """Credencial da API interna de ingestão de mensagens.

    O tenant vem daqui, nunca de query parameter. O token é guardado apenas
    como hash.
    """
    create_table(
        cursor,
        "tenant_api_credentials",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            token_prefix TEXT NOT NULL,
            description TEXT,
            scopes_json TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            revoked_at TIMESTAMP,
            last_used_at TIMESTAMP
        """,
    )
    create_unique_index(
        cursor, "idx_tenant_api_credentials_hash", "tenant_api_credentials", "token_hash"
    )
    create_index(
        cursor,
        "idx_tenant_api_credentials_company",
        "tenant_api_credentials",
        "company_id, active",
    )


def _add_message_example_lineage(cursor: Any) -> None:
    """Lineage estruturado do exemplo até a conversa de origem.

    `source_reference` continua existindo por compatibilidade, mas deixa de
    carregar o papel de agrupamento — que agora é `source_group_key`.
    """
    ensure_column(cursor, "message_examples", "source_group_key", "TEXT")
    ensure_column(cursor, "message_examples", "conversation_context_json", "TEXT")
    ensure_column(cursor, "message_examples", "source_batch_id", "INTEGER")
    ensure_column(cursor, "message_examples", "source_conversation_id", "INTEGER")
    ensure_column(cursor, "message_examples", "source_candidate_id", "INTEGER")
    ensure_column(cursor, "message_examples", "approved_by", "TEXT")
    ensure_column(cursor, "message_examples", "approved_at", "TIMESTAMP")

    create_index(
        cursor,
        "idx_message_examples_source_group",
        "message_examples",
        "company_id, source_group_key",
    )
    # Garante que um candidato só possa ser promovido uma vez.
    create_unique_index(
        cursor,
        "idx_message_examples_source_candidate",
        "message_examples",
        "company_id, source_candidate_id",
    )


def _add_evaluation_run_provenance(cursor: Any) -> None:
    """Proveniência verificável do sujeito avaliado.

    Sem isso, uma execução simulada podia ser registrada como `finetuned` e
    liberar promoção.
    """
    ensure_column(
        cursor, "evaluation_runs", "execution_mode", "TEXT NOT NULL DEFAULT 'simulation'"
    )
    ensure_column(
        cursor, "evaluation_runs", "promotion_eligible", "INTEGER NOT NULL DEFAULT 0"
    )
    ensure_column(cursor, "evaluation_runs", "base_model", "TEXT")
    ensure_column(cursor, "evaluation_runs", "finetuning_job_id", "TEXT")
    ensure_column(cursor, "evaluation_runs", "prompt_contract_version", "TEXT")
    ensure_column(cursor, "evaluation_runs", "retrieval_config_version", "TEXT")
    ensure_column(cursor, "evaluation_runs", "case_suite_hash", "TEXT")
    ensure_column(cursor, "evaluation_runs", "subject_fingerprint", "TEXT")

    create_index(
        cursor,
        "idx_evaluation_runs_promotion",
        "evaluation_runs",
        "company_id, approach, promotion_eligible, created_at",
    )


def _add_knowledge_published_version(cursor: Any) -> None:
    """Separa versão publicada da versão em edição.

    Uma nova versão em draft não pode tirar do ar a versão que a produção
    está usando.
    """
    ensure_column(cursor, "knowledge_documents", "current_published_version_id", "INTEGER")
    ensure_column(cursor, "knowledge_documents", "working_version_id", "INTEGER")
    ensure_column(cursor, "knowledge_documents", "metadata_hash", "TEXT")

    create_index(
        cursor,
        "idx_knowledge_documents_published_version",
        "knowledge_documents",
        "company_id, current_published_version_id",
    )

    # Documentos já aprovados antes desta migration continuam publicados.
    cursor.execute(
        """
        UPDATE knowledge_documents
        SET current_published_version_id = current_version_id
        WHERE current_published_version_id IS NULL
          AND status = 'approved'
        """
    )
    cursor.execute(
        """
        UPDATE knowledge_documents
        SET working_version_id = current_version_id
        WHERE working_version_id IS NULL
        """
    )


def _add_company_model_lifecycle(cursor: Any) -> None:
    """Estado do job refletido no candidato de modelo."""
    ensure_column(cursor, "company_models", "job_status", "TEXT")
    ensure_column(cursor, "company_models", "failure_reason", "TEXT")
    ensure_column(cursor, "company_models", "promoted_evaluation_run_id", "INTEGER")

    create_index(
        cursor,
        "idx_company_models_job",
        "company_models",
        "company_id, finetuning_job_id",
    )


def _add_import_lineage_indexes(cursor: Any) -> None:
    """Índices para o upsert estável do staging."""
    create_index(
        cursor,
        "idx_training_candidates_conversation",
        "training_candidates",
        "company_id, conversation_id",
    )
    create_index(
        cursor,
        "idx_imported_conversations_company_external",
        "imported_conversations",
        "company_id, external_conversation_id",
    )
    ensure_column(cursor, "training_candidates", "promotion_state", "TEXT NOT NULL DEFAULT 'none'")
    cursor.execute(
        """
        UPDATE training_candidates
        SET promotion_state = 'promoted'
        WHERE promotion_state = 'none'
          AND promoted_message_example_id IS NOT NULL
        """
    )


IDENTITY_MIGRATIONS: list[Migration] = [
    (
        "038_platform_identity",
        "Create platform principals, access tokens and company memberships",
        _create_platform_identity,
    ),
    (
        "039_tenant_api_credentials",
        "Create tenant-scoped credentials for the message ingest API",
        _create_tenant_api_credentials,
    ),
    (
        "040_message_example_lineage",
        "Add structured lineage and conversation context to message examples",
        _add_message_example_lineage,
    ),
    (
        "041_evaluation_run_provenance",
        "Add execution mode and verifiable provenance to evaluation runs",
        _add_evaluation_run_provenance,
    ),
    (
        "042_knowledge_published_version",
        "Separate published version from working version on knowledge documents",
        _add_knowledge_published_version,
    ),
    (
        "043_company_model_lifecycle",
        "Track fine-tuning job status on company model candidates",
        _add_company_model_lifecycle,
    ),
    (
        "044_import_lineage_indexes",
        "Add import lineage indexes and candidate promotion state",
        _add_import_lineage_indexes,
    ),
]
