"""Migrations 045–046 — endurecimento de identidade e versionamento de knowledge.

Aditivas, idempotentes e compatíveis com SQLite e Postgres. Não alteram
nenhuma migration já aplicada (024–044).

045 dá à sessão uma âncora verificável no banco: sem isso, revogar a
credencial que criou a sessão não invalidava o cookie já emitido.

046 move os metadados que influenciam retrieval para a versão do documento.
Enquanto eles viviam na linha do documento, editar um rascunho mudava o
título, o tipo, a prioridade e a vigência da versão que estava no ar.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import create_index, ensure_column

Migration = tuple[str, str, Callable[[Any], None]]

HARDENING_ID_TABLES: set[str] = set()

# Metadados que alteram o resultado do retrieval e, por isso, pertencem à
# versão. `content_hash` continua fora: ele identifica só o conteúdo.
VERSIONED_KNOWLEDGE_METADATA: tuple[str, ...] = (
    "title",
    "document_type",
    "priority",
    "valid_from",
    "valid_until",
    "source_uri",
    "language",
)


def _add_principal_auth_epoch(cursor: Any) -> None:
    """Época de autenticação por principal e vínculo sessão -> credencial.

    A sessão passa a carregar `token_id` e `auth_epoch`. Qualquer um dos três
    eventos — desativar o principal, revogar a credencial, incrementar a época
    — invalida imediatamente os cookies já emitidos, sem esperar o TTL.
    """
    ensure_column(cursor, "platform_principals", "auth_epoch", "INTEGER NOT NULL DEFAULT 1")
    create_index(
        cursor,
        "idx_platform_principals_active_epoch",
        "platform_principals",
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
        "045_principal_auth_epoch",
        "Add an auth epoch so revoking a credential invalidates existing sessions",
        _add_principal_auth_epoch,
    ),
    (
        "046_versioned_knowledge_metadata",
        "Move retrieval-affecting knowledge metadata onto the document version",
        _version_knowledge_metadata,
    ),
    (
        "047_evaluation_executor_attestation",
        "Record which executor produced each evaluation run",
        _add_evaluation_executor_attestation,
    ),
]
