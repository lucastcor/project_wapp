"""Persistência de documentos, versões e chunks de conhecimento.

Todo acesso exige `company_id`; o filtro entra na query, antes de qualquer
ranking, para que nenhum chunk de outro tenant chegue a ser pontuado.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import (
    execute,
    execute_rowcount,
    fetch_all,
    fetch_one,
    optional_equals,
    transaction,
)

DOCUMENT_STATUS_DRAFT = "draft"
DOCUMENT_STATUS_APPROVED = "approved"
DOCUMENT_STATUS_ARCHIVED = "archived"

_DOCUMENT_COLUMNS = """
    id,
    company_id,
    external_ref,
    title,
    source_type,
    document_type,
    status,
    priority,
    valid_from,
    valid_until,
    current_version_id,
    current_published_version_id,
    working_version_id,
    metadata_hash,
    approved_by,
    approved_at,
    created_by,
    created_at,
    updated_at
"""


# Metadados que mudam o resultado do retrieval. Eles pertencem à **versão**:
# renomear ou repriorizar um rascunho não pode alterar o que está no ar.
_VERSION_COLUMNS = """
    id,
    company_id,
    document_id,
    version,
    content,
    content_hash,
    status,
    title,
    document_type,
    priority,
    valid_from,
    valid_until,
    source_uri,
    language,
    author,
    created_at
"""


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def metadata_hash(
    *,
    title: str,
    document_type: str,
    source_type: str,
    priority: int,
    valid_from: str | None,
    valid_until: str | None,
) -> str:
    payload = json.dumps(
        {
            "title": title,
            "document_type": document_type,
            "source_type": source_type,
            "priority": int(priority),
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _version_metadata_hash(version: dict[str, Any], source_type: str) -> str:
    """Hash dos metadados **como gravados na versão**."""
    return metadata_hash(
        title=str(version.get("title") or ""),
        document_type=str(version.get("document_type") or ""),
        source_type=source_type,
        priority=int(version.get("priority") or 0),
        valid_from=version.get("valid_from"),
        valid_until=version.get("valid_until"),
    )


def upsert_document(
    *,
    company_id: int,
    title: str,
    content: str,
    document_type: str,
    external_ref: str | None = None,
    source_type: str = "manual",
    priority: int = 0,
    valid_from: str | None = None,
    valid_until: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Cria ou versiona um documento. Idempotente por `external_ref`.

    Três garantias:

    1. **Conteúdo e metadados idênticos são no-op.** A comparação de hash
       acontece ANTES de qualquer escrita, então reenviar o mesmo documento não
       altera versão, status nem `updated_at`.
    2. **Status não muda como efeito colateral.** Aprovar, arquivar e publicar
       são comandos próprios. Uma nova versão de conteúdo entra como `draft` e
       a versão publicada continua no ar até ser aprovada explicitamente.
    3. **Metadados pertencem à versão.** Título, tipo, prioridade e vigência
       são gravados na versão nova. As colunas equivalentes no documento são
       espelho da versão *publicada* e só se movem em `approve_document` —
       antes, editar um rascunho reescrevia o metadado que estava no ar.
    """
    resolved_company_id = require_company_id(company_id, context="knowledge document write")
    normalized_content = str(content or "").strip()
    if not normalized_content:
        raise ValueError("Knowledge document content must not be blank.")

    existing = None
    if external_ref:
        existing = fetch_one(
            f"SELECT {_DOCUMENT_COLUMNS} FROM knowledge_documents WHERE company_id = ? AND external_ref = ?",
            (resolved_company_id, external_ref),
        )

    new_content_hash = content_hash(normalized_content)
    new_metadata_hash = metadata_hash(
        title=title,
        document_type=document_type,
        source_type=source_type,
        priority=priority,
        valid_from=valid_from,
        valid_until=valid_until,
    )

    if existing is None:
        document_id = execute(
            """
            INSERT INTO knowledge_documents (
                company_id,
                external_ref,
                title,
                source_type,
                document_type,
                status,
                priority,
                valid_from,
                valid_until,
                metadata_hash,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_company_id,
                external_ref,
                title,
                source_type,
                document_type,
                DOCUMENT_STATUS_DRAFT,
                int(priority),
                valid_from,
                valid_until,
                new_metadata_hash,
                author,
            ),
        )
    else:
        document_id = int(existing["id"])
        working_version = get_working_version(
            company_id=resolved_company_id,
            document_id=document_id,
        )
        unchanged_content = (
            working_version is not None
            and str(working_version.get("content_hash")) == new_content_hash
        )
        # A comparação é contra os metadados da versão em edição, não contra o
        # espelho do documento: é a versão que carrega a verdade.
        unchanged_metadata = (
            working_version is not None
            and _version_metadata_hash(working_version, source_type) == new_metadata_hash
        )

        if unchanged_content and unchanged_metadata and working_version is not None:
            # No-op verdadeiro: nada é escrito.
            return {
                "document_id": document_id,
                "version_id": int(working_version["id"]),
                "version": int(working_version["version"]),
                "created_version": False,
                "unchanged": True,
                "status": str(existing.get("status") or DOCUMENT_STATUS_DRAFT),
                "published_version_id": existing.get("current_published_version_id"),
            }

        published_version_id = existing.get("current_published_version_id")
        working_version_is_published = (
            working_version is not None
            and published_version_id is not None
            and int(working_version["id"]) == int(published_version_id)
        )

        if unchanged_content and working_version is not None and not working_version_is_published:
            # Só metadado mudou e a versão em edição ainda não está no ar:
            # atualiza a própria versão em vez de criar outra.
            execute(
                """
                UPDATE knowledge_document_versions
                SET title = ?,
                    document_type = ?,
                    priority = ?,
                    valid_from = ?,
                    valid_until = ?
                WHERE id = ?
                  AND company_id = ?
                """,
                (
                    title,
                    document_type,
                    int(priority),
                    valid_from,
                    valid_until,
                    int(working_version["id"]),
                    resolved_company_id,
                ),
            )
            return {
                "document_id": document_id,
                "version_id": int(working_version["id"]),
                "version": int(working_version["version"]),
                "created_version": False,
                "unchanged": False,
                "status": str(existing.get("status") or DOCUMENT_STATUS_DRAFT),
                "published_version_id": published_version_id,
            }

    next_version = _next_document_version(resolved_company_id, document_id)
    version_id = execute(
        """
        INSERT INTO knowledge_document_versions (
            company_id,
            document_id,
            version,
            content,
            content_hash,
            status,
            title,
            document_type,
            priority,
            valid_from,
            valid_until,
            author
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            document_id,
            next_version,
            normalized_content,
            new_content_hash,
            DOCUMENT_STATUS_DRAFT,
            title,
            document_type,
            int(priority),
            valid_from,
            valid_until,
            author,
        ),
    )
    # `working_version_id` avança; `current_published_version_id` não é tocado,
    # então a produção continua servindo a versão aprovada.
    execute(
        """
        UPDATE knowledge_documents
        SET current_version_id = ?,
            working_version_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (version_id, version_id, document_id, resolved_company_id),
    )

    document = fetch_one(
        f"SELECT {_DOCUMENT_COLUMNS} FROM knowledge_documents WHERE id = ? AND company_id = ?",
        (document_id, resolved_company_id),
    )
    return {
        "document_id": document_id,
        "version_id": version_id,
        "version": next_version,
        "created_version": True,
        "unchanged": False,
        "status": str((document or {}).get("status") or DOCUMENT_STATUS_DRAFT),
        "published_version_id": (document or {}).get("current_published_version_id"),
    }


def get_working_version(*, company_id: int, document_id: int) -> dict[str, Any] | None:
    """Versão em edição (a mais recente), publicada ou não."""
    resolved_company_id = require_company_id(company_id, context="knowledge version read")
    return fetch_one(
        f"""
        SELECT {_VERSION_COLUMNS}
        FROM knowledge_document_versions
        WHERE company_id = ?
          AND document_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (resolved_company_id, document_id),
    )


def _next_document_version(company_id: int, document_id: int) -> int:
    row = fetch_one(
        """
        SELECT MAX(version) AS max_version
        FROM knowledge_document_versions
        WHERE company_id = ?
          AND document_id = ?
        """,
        (company_id, document_id),
    )
    return int((row or {}).get("max_version") or 0) + 1


def get_current_version(*, company_id: int, document_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="knowledge version read")
    return fetch_one(
        f"""
        SELECT {_VERSION_COLUMNS}
        FROM knowledge_document_versions
        WHERE company_id = ?
          AND document_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (resolved_company_id, document_id),
    )


def get_document(*, company_id: int, document_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="knowledge document read")
    return fetch_one(
        f"SELECT {_DOCUMENT_COLUMNS} FROM knowledge_documents WHERE id = ? AND company_id = ?",
        (document_id, resolved_company_id),
    )


def list_documents(
    *,
    company_id: int,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="knowledge document listing")
    status_condition, status_params = optional_equals("status", status)
    return fetch_all(
        f"""
        SELECT {_DOCUMENT_COLUMNS}
        FROM knowledge_documents
        WHERE company_id = ?
          AND {status_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *status_params, limit),
    )


def approve_document(
    *,
    company_id: int,
    document_id: int,
    approved_by: str | None = None,
    version_id: int | None = None,
) -> bool:
    """Publica explicitamente uma versão do documento.

    Sem `version_id`, publica a versão em edição. A publicação é o único
    caminho que move `current_published_version_id` — e também o único que
    move os metadados espelhados no documento, porque eles descrevem o que
    está no ar.
    """
    resolved_company_id = require_company_id(company_id, context="knowledge document approval")
    target_version = (
        fetch_one(
            f"""
            SELECT {_VERSION_COLUMNS} FROM knowledge_document_versions
            WHERE company_id = ? AND document_id = ? AND id = ?
            """,
            (resolved_company_id, document_id, version_id),
        )
        if version_id is not None
        else get_working_version(company_id=resolved_company_id, document_id=document_id)
    )
    if target_version is None:
        return False

    updated = execute_rowcount(
        """
        UPDATE knowledge_documents
        SET status = ?,
            current_published_version_id = ?,
            title = ?,
            document_type = ?,
            priority = ?,
            valid_from = ?,
            valid_until = ?,
            approved_by = ?,
            approved_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (
            DOCUMENT_STATUS_APPROVED,
            int(target_version["id"]),
            target_version.get("title"),
            target_version.get("document_type"),
            int(target_version.get("priority") or 0),
            target_version.get("valid_from"),
            target_version.get("valid_until"),
            approved_by,
            document_id,
            resolved_company_id,
        ),
    )
    if not updated:
        return False

    execute_rowcount(
        """
        UPDATE knowledge_document_versions
        SET status = ?
        WHERE company_id = ?
          AND id = ?
        """,
        (DOCUMENT_STATUS_APPROVED, resolved_company_id, int(target_version["id"])),
    )
    return True


def rollback_published_version(
    *,
    company_id: int,
    document_id: int,
    version_id: int,
) -> bool:
    """Volta a publicação para uma versão anterior já existente."""
    return approve_document(
        company_id=company_id,
        document_id=document_id,
        version_id=version_id,
        approved_by="rollback",
    )


def list_document_versions(*, company_id: int, document_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="knowledge version listing")
    return fetch_all(
        """
        SELECT id, company_id, document_id, version, content_hash, status,
               title, document_type, priority, valid_from, valid_until,
               author, created_at
        FROM knowledge_document_versions
        WHERE company_id = ?
          AND document_id = ?
        ORDER BY version DESC
        """,
        (resolved_company_id, document_id),
    )


def get_version(*, company_id: int, version_id: int) -> dict[str, Any] | None:
    """Uma versão específica, com seus metadados de retrieval."""
    resolved_company_id = require_company_id(company_id, context="knowledge version read")
    return fetch_one(
        f"""
        SELECT {_VERSION_COLUMNS}
        FROM knowledge_document_versions
        WHERE company_id = ?
          AND id = ?
        """,
        (resolved_company_id, version_id),
    )


def archive_document(*, company_id: int, document_id: int) -> bool:
    resolved_company_id = require_company_id(company_id, context="knowledge document archive")
    return (
        execute_rowcount(
            """
            UPDATE knowledge_documents
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (DOCUMENT_STATUS_ARCHIVED, document_id, resolved_company_id),
        )
        > 0
    )


def append_chunks(
    *,
    company_id: int,
    document_id: int,
    document_version_id: int,
    chunks: list[dict[str, Any]],
) -> int:
    """Grava os chunks de uma versão específica.

    Chunks de versões anteriores são preservados: a versão publicada continua
    recuperável enquanto um draft novo existe.
    """
    resolved_company_id = require_company_id(company_id, context="knowledge chunk write")
    with transaction() as cursor:
        cursor.execute(
            """
            DELETE FROM knowledge_chunks
            WHERE company_id = ?
              AND document_id = ?
              AND document_version_id = ?
            """,
            (resolved_company_id, document_id, document_version_id),
        )
        for index, chunk in enumerate(chunks):
            embedding = chunk.get("embedding")
            cursor.execute(
                """
                INSERT INTO knowledge_chunks (
                    company_id,
                    document_id,
                    document_version_id,
                    chunk_index,
                    content,
                    search_text,
                    token_estimate,
                    embedding_model,
                    embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_company_id,
                    document_id,
                    document_version_id,
                    index,
                    chunk["content"],
                    chunk["search_text"],
                    int(chunk.get("token_estimate") or 0),
                    chunk.get("embedding_model"),
                    json.dumps(embedding) if embedding else None,
                ),
            )
    return len(chunks)


def get_retrievable_chunks(
    *,
    company_id: int,
    now_iso: str,
    document_types: list[str] | None = None,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Chunks candidatos do tenant: documento aprovado e dentro da vigência.

    Draft, arquivado, não aprovado ou expirado nunca sai daqui.

    Conteúdo **e** metadados vêm da versão publicada (`v`). O documento entra
    só para o status e o `source_type`: enquanto vigência e prioridade eram
    lidas de `d`, um rascunho podia expirar ou repriorizar o que estava no ar.
    """
    resolved_company_id = require_company_id(company_id, context="knowledge retrieval")

    conditions = [
        "c.company_id = ?",
        "d.company_id = ?",
        "d.status = ?",
        "(v.valid_from IS NULL OR v.valid_from <= ?)",
        "(v.valid_until IS NULL OR v.valid_until >= ?)",
        # Só a versão publicada chega ao runtime: um draft novo não tira a
        # versão aprovada do ar.
        "c.document_version_id = d.current_published_version_id",
    ]
    params: list[Any] = [
        resolved_company_id,
        resolved_company_id,
        DOCUMENT_STATUS_APPROVED,
        now_iso,
        now_iso,
    ]

    if document_types:
        placeholders = ", ".join("?" for _ in document_types)
        conditions.append(f"v.document_type IN ({placeholders})")
        params.extend(document_types)

    params.append(limit)

    return fetch_all(
        f"""
        SELECT
            c.id AS chunk_id,
            c.company_id AS company_id,
            c.document_id AS document_id,
            c.document_version_id AS document_version_id,
            c.chunk_index AS chunk_index,
            c.content AS content,
            c.search_text AS search_text,
            c.embedding_model AS embedding_model,
            c.embedding_json AS embedding_json,
            v.title AS document_title,
            v.document_type AS document_type,
            v.priority AS priority,
            v.valid_until AS valid_until,
            d.source_type AS source_type
        FROM knowledge_chunks c
        JOIN knowledge_documents d ON d.id = c.document_id
        JOIN knowledge_document_versions v
            ON v.id = d.current_published_version_id
           AND v.company_id = c.company_id
        WHERE {' AND '.join(conditions)}
        ORDER BY v.priority DESC, c.document_id DESC, c.chunk_index ASC
        LIMIT ?
        """,
        tuple(params),
    )


def count_chunks(*, company_id: int) -> int:
    resolved_company_id = require_company_id(company_id, context="knowledge chunk count")
    row = fetch_one(
        "SELECT COUNT(*) AS total FROM knowledge_chunks WHERE company_id = ?",
        (resolved_company_id,),
    )
    return int((row or {}).get("total") or 0)
