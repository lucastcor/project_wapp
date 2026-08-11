"""Migrations 058-060 — invalidação de sessão, metadados e atestação.

Aditivas, idempotentes e compatíveis com SQLite e Postgres.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import create_index, ensure_column

Migration = tuple[str, str, Callable[[Any], None]]


def _add_identity_auth_epoch(cursor: Any) -> None:
    """Época de autenticação para pessoas e para service accounts.

    A branch de origem aplicava isto a `platform_principals`. A ideia é boa e
    vale para o modelo canônico: sem uma época, revogar acesso só tem efeito
    quando o TTL da sessão expira, e uma sessão já emitida continua válida
    depois de a pessoa ser desativada ou de a senha ser trocada.

    Com a época, três eventos passam a invalidar imediatamente todo cookie já
    emitido, sem varrer `user_sessions`: desativar a conta, trocar a senha e
    incrementar a época explicitamente. A verificação é uma comparação de
    inteiro no caminho de autenticação que já carrega o usuário.

    O default 1 preserva o comportamento das sessões existentes: elas foram
    emitidas antes da coluna existir e continuam válidas até o TTL.
    """
    ensure_column(cursor, "saas_users", "auth_epoch", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "user_sessions", "auth_epoch", "INTEGER NOT NULL DEFAULT 1")
    create_index(cursor, "idx_saas_users_active_epoch", "saas_users", "id, active, auth_epoch")

    ensure_column(cursor, "service_accounts", "auth_epoch", "INTEGER NOT NULL DEFAULT 1")
    create_index(
        cursor,
        "idx_service_accounts_active_epoch",
        "service_accounts",
        "id, active, auth_epoch",
    )


def _version_knowledge_metadata(cursor: Any) -> None:
    """Metadados de retrieval passam a pertencer à versão do documento."""
    ensure_column(cursor, "knowledge_document_versions", "title", "TEXT")
    ensure_column(cursor, "knowledge_document_versions", "document_type", "TEXT")
    ensure_column(
        cursor, "knowledge_document_versions", "priority", "INTEGER NOT NULL DEFAULT 0"
    )
    ensure_column(cursor, "knowledge_document_versions", "valid_from", "TEXT")
    ensure_column(cursor, "knowledge_document_versions", "valid_until", "TEXT")
    ensure_column(cursor, "knowledge_document_versions", "source_uri", "TEXT")
    ensure_column(cursor, "knowledge_document_versions", "language", "TEXT")

    # Retrofit: versões existentes herdam o metadado que o documento carregava.
    # É a única interpretação possível — antes desta migration havia um único
    # conjunto de metadados por documento.
    cursor.execute(
        """
        UPDATE knowledge_document_versions
        SET title = (
                SELECT d.title FROM knowledge_documents d
                WHERE d.id = knowledge_document_versions.document_id
            ),
            document_type = (
                SELECT d.document_type FROM knowledge_documents d
                WHERE d.id = knowledge_document_versions.document_id
            ),
            priority = COALESCE((
                SELECT d.priority FROM knowledge_documents d
                WHERE d.id = knowledge_document_versions.document_id
            ), 0),
            valid_from = (
                SELECT d.valid_from FROM knowledge_documents d
                WHERE d.id = knowledge_document_versions.document_id
            ),
            valid_until = (
                SELECT d.valid_until FROM knowledge_documents d
                WHERE d.id = knowledge_document_versions.document_id
            )
        WHERE title IS NULL
        """
    )

    create_index(
        cursor,
        "idx_knowledge_versions_document_metadata",
        "knowledge_document_versions",
        "company_id, document_id, document_type, priority",
    )


def _add_evaluation_executor_attestation(cursor: Any) -> None:
    """Quem executou a avaliação, registrado junto com o resultado.

    `execution_mode = model_backed` deixava de ser verificável porque nada
    dizia *qual* executor produziu as respostas. Um gerador determinístico
    injetado ficava indistinguível de uma chamada ao modelo real.
    """
    ensure_column(cursor, "evaluation_runs", "executor_kind", "TEXT")
    ensure_column(cursor, "evaluation_runs", "provider", "TEXT")
    ensure_column(cursor, "evaluation_runs", "attestation_json", "TEXT")

    create_index(
        cursor,
        "idx_evaluation_runs_executor",
        "evaluation_runs",
        "company_id, executor_kind, promotion_eligible",
    )


HARDENING_MIGRATIONS: list[Migration] = [
    (
        "058_identity_auth_epoch",
        "Add authentication epoch to users, sessions and service accounts",
        _add_identity_auth_epoch,
    ),
    (
        "059_versioned_knowledge_metadata",
        "Move retrieval metadata onto knowledge document versions",
        _version_knowledge_metadata,
    ),
    (
        "060_evaluation_executor_attestation",
        "Record executor attestation on evaluation runs",
        _add_evaluation_executor_attestation,
    ),
]
