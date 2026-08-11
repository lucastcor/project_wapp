"""Migrations 052-057 — service accounts, proveniência e lineage.

Todas aditivas, idempotentes e compatíveis com SQLite e Postgres.

## Por que não há `platform_principals` aqui

A branch de origem criava `platform_principals`, `platform_access_tokens` e
`platform_company_memberships`: um segundo diretório de pessoas, paralelo ao
canônico (`saas_users` + `company_memberships`). Dois diretórios de identidade
humana no mesmo produto significam duas superfícies de login, dois lugares para
desativar alguém que saiu da empresa e duas respostas possíveis para "quem pode
ver esta empresa" — divergentes no dia em que uma delas for esquecida.

O modelo humano canônico permanece único. O que a branch resolvia de legítimo
era o acesso **de máquina** (ingestão de mensagens, workers, integrações), e
isso é modelado aqui explicitamente como service account: uma identidade não
humana, sempre presa a uma empresa, que consome o mesmo vocabulário de papéis
(`owner`/`admin`/`operator`/`viewer`) e a mesma abstração de autorização das
pessoas. Um service account não tem senha, não faz login interativo e não
aparece no diretório de pessoas.
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
    "service_accounts",
    "service_account_tokens",
}


def _create_service_accounts(cursor: Any) -> None:
    """Identidade de máquina, sempre escopada a uma empresa.

    Substitui `platform_principals`/`platform_access_tokens` da branch de
    origem. Três diferenças deliberadas:

    1. `company_id` é NOT NULL e referencia `companies`. Não existe service
       account global: um token vazado alcança uma empresa, nunca a
       plataforma inteira.
    2. `role` usa o mesmo vocabulário de `company_memberships`, então a
       autorização de rota não precisa saber se o chamador é pessoa ou
       máquina — só precisa do par (company_id, role).
    3. O segredo existe apenas como hash. `token_prefix` guarda os primeiros
       caracteres em claro para o operador identificar a credencial na UI e
       nos logs sem que o valor completo seja recuperável.
    """
    create_table(
        cursor,
        "service_accounts",
        """
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_by_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "service_account_tokens",
        """
            id ID_PK,
            service_account_id INTEGER NOT NULL REFERENCES service_accounts(id) ON DELETE CASCADE,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL,
            token_prefix TEXT NOT NULL,
            description TEXT,
            scopes_json TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            created_by_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            revoked_at TIMESTAMP,
            last_used_at TIMESTAMP
        """,
    )

    # O slug identifica o service account para o operador; único por empresa,
    # não globalmente, para que uma empresa não descubra os nomes de outra ao
    # colidir num slug.
    create_unique_index(
        cursor, "idx_service_accounts_company_slug", "service_accounts", "company_id, slug"
    )
    create_index(
        cursor, "idx_service_accounts_company_active", "service_accounts", "company_id, active"
    )
    # O hash é globalmente único: é a chave de lookup na autenticação, feita
    # antes de qualquer empresa ser conhecida.
    create_unique_index(
        cursor, "idx_service_account_tokens_hash", "service_account_tokens", "token_hash"
    )
    create_index(
        cursor,
        "idx_service_account_tokens_account",
        "service_account_tokens",
        "service_account_id, active",
    )
    create_index(
        cursor,
        "idx_service_account_tokens_company",
        "service_account_tokens",
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
        "052_service_accounts",
        "Create company-scoped service accounts and hashed tokens",
        _create_service_accounts,
    ),
    (
        "053_message_example_lineage",
        "Add structured lineage and conversation context to message examples",
        _add_message_example_lineage,
    ),
    (
        "054_evaluation_run_provenance",
        "Add execution mode and verifiable provenance to evaluation runs",
        _add_evaluation_run_provenance,
    ),
    (
        "055_knowledge_published_version",
        "Separate published version from working version on knowledge documents",
        _add_knowledge_published_version,
    ),
    (
        "056_company_model_lifecycle",
        "Track fine-tuning job status on company model candidates",
        _add_company_model_lifecycle,
    ),
    (
        "057_import_lineage_indexes",
        "Add import lineage indexes and candidate promotion state",
        _add_import_lineage_indexes,
    ),
]
