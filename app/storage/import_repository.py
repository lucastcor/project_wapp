"""Staging de importação de conversas e candidatos de treino.

Tabelas separadas dos dados operacionais: conteúdo importado é não confiável
até passar por anonimização e revisão humana.
"""

from __future__ import annotations

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

BATCH_STATUS_RECEIVED = "received"
BATCH_STATUS_STAGED = "staged"
BATCH_STATUS_FAILED = "failed"

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"

_BATCH_COLUMNS = """
    id,
    company_id,
    batch_key,
    source_format,
    source_label,
    status,
    dry_run,
    stats_json,
    error_detail,
    created_by,
    created_at,
    updated_at
"""

_CANDIDATE_COLUMNS = """
    id,
    company_id,
    batch_id,
    conversation_id,
    candidate_hash,
    group_key,
    scenario_type,
    customer_profile,
    profile_name,
    context_json,
    customer_message,
    approved_reply,
    quality_score,
    pii_report_json,
    review_status,
    review_notes,
    reviewed_by,
    reviewed_at,
    promoted_message_example_id,
    promotion_state,
    created_at,
    updated_at
"""


def get_batch_by_key(*, company_id: int, batch_key: str) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="import batch read")
    return fetch_one(
        f"SELECT {_BATCH_COLUMNS} FROM conversation_import_batches WHERE company_id = ? AND batch_key = ?",
        (resolved_company_id, batch_key),
    )


def get_batch(*, company_id: int, batch_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="import batch read")
    return fetch_one(
        f"SELECT {_BATCH_COLUMNS} FROM conversation_import_batches WHERE id = ? AND company_id = ?",
        (batch_id, resolved_company_id),
    )


def list_batches(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="import batch listing")
    return fetch_all(
        f"""
        SELECT {_BATCH_COLUMNS}
        FROM conversation_import_batches
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, limit),
    )


def create_batch(
    *,
    company_id: int,
    batch_key: str,
    source_format: str,
    source_label: str | None,
    dry_run: bool,
    created_by: str | None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="import batch creation")
    return execute(
        """
        INSERT INTO conversation_import_batches (
            company_id,
            batch_key,
            source_format,
            source_label,
            status,
            dry_run,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            batch_key,
            source_format,
            source_label,
            BATCH_STATUS_RECEIVED,
            int(dry_run),
            created_by,
        ),
    )


def update_batch_status(
    *,
    company_id: int,
    batch_id: int,
    status: str,
    stats: dict[str, Any] | None = None,
    error_detail: str | None = None,
) -> None:
    resolved_company_id = require_company_id(company_id, context="import batch update")
    execute(
        """
        UPDATE conversation_import_batches
        SET status = ?,
            stats_json = COALESCE(?, stats_json),
            error_detail = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (
            status,
            json.dumps(stats, ensure_ascii=False) if stats is not None else None,
            error_detail,
            batch_id,
            resolved_company_id,
        ),
    )


def upsert_batch_staging(
    *,
    company_id: int,
    batch_id: int,
    conversations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Regrava o staging do lote preservando a identidade das conversas.

    A chave estável é `(company_id, batch_id, external_conversation_id)`. A
    conversa é atualizada no lugar, não apagada e recriada: apagar invalidaria
    o `conversation_id` referenciado pelos candidatos já extraídos.

    Conversas que sumiram do novo conteúdo são removidas apenas depois de os
    candidatos que apontavam para elas serem desligados, para não deixar
    referência órfã. Tudo em uma única transação.
    """
    resolved_company_id = require_company_id(company_id, context="import staging write")

    conversation_ids: dict[str, int] = {}
    message_count = 0
    created = 0
    updated = 0

    with transaction() as cursor:
        cursor.execute(
            """
            SELECT id, external_conversation_id
            FROM imported_conversations
            WHERE company_id = ?
              AND batch_id = ?
            """,
            (resolved_company_id, batch_id),
        )
        existing = {
            str(row["external_conversation_id"]): int(row["id"]) for row in cursor.fetchall()
        }

        for conversation in conversations:
            external_id = str(conversation["external_conversation_id"])
            message_rows = conversation.get("messages") or []
            conversation_id = existing.get(external_id)

            if conversation_id is None:
                cursor.execute(
                    """
                    INSERT INTO imported_conversations (
                        company_id,
                        batch_id,
                        external_conversation_id,
                        contact_ref_hash,
                        session_index,
                        started_at,
                        ended_at,
                        message_count,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_company_id,
                        batch_id,
                        external_id,
                        conversation["contact_ref_hash"],
                        int(conversation.get("session_index") or 0),
                        conversation.get("started_at"),
                        conversation.get("ended_at"),
                        len(message_rows),
                        "staged",
                    ),
                )
                conversation_id = int(cursor.lastrowid or 0)
                created += 1
            else:
                cursor.execute(
                    """
                    UPDATE imported_conversations
                    SET contact_ref_hash = ?,
                        session_index = ?,
                        started_at = ?,
                        ended_at = ?,
                        message_count = ?,
                        status = 'staged'
                    WHERE id = ?
                      AND company_id = ?
                    """,
                    (
                        conversation["contact_ref_hash"],
                        int(conversation.get("session_index") or 0),
                        conversation.get("started_at"),
                        conversation.get("ended_at"),
                        len(message_rows),
                        conversation_id,
                        resolved_company_id,
                    ),
                )
                # As mensagens são reescritas: o conteúdo é derivado do
                # arquivo e a conversa mantém o mesmo id.
                cursor.execute(
                    """
                    DELETE FROM imported_messages
                    WHERE company_id = ?
                      AND conversation_id = ?
                    """,
                    (resolved_company_id, conversation_id),
                )
                updated += 1

            conversation_ids[external_id] = conversation_id

            for sequence, message in enumerate(message_rows, start=1):
                cursor.execute(
                    """
                    INSERT INTO imported_messages (
                        company_id,
                        batch_id,
                        conversation_id,
                        sequence,
                        role,
                        anonymized_content,
                        pii_report_json,
                        occurred_at,
                        source_line
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_company_id,
                        batch_id,
                        conversation_id,
                        sequence,
                        message["role"],
                        message["anonymized_content"],
                        json.dumps(message.get("pii_report") or {}, ensure_ascii=False),
                        message.get("occurred_at"),
                        message.get("source_line"),
                    ),
                )
                message_count += 1

        obsolete_ids = [
            conversation_id
            for external_id, conversation_id in existing.items()
            if external_id not in conversation_ids
        ]
        for conversation_id in obsolete_ids:
            # Desliga o candidato antes de remover a conversa: preferimos um
            # candidato sem conversa a um candidato apontando para um id morto.
            cursor.execute(
                """
                UPDATE training_candidates
                SET conversation_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE company_id = ?
                  AND conversation_id = ?
                """,
                (resolved_company_id, conversation_id),
            )
            cursor.execute(
                "DELETE FROM imported_messages WHERE company_id = ? AND conversation_id = ?",
                (resolved_company_id, conversation_id),
            )
            cursor.execute(
                "DELETE FROM imported_conversations WHERE company_id = ? AND id = ?",
                (resolved_company_id, conversation_id),
            )

    return {
        "conversations": len(conversation_ids),
        "messages": message_count,
        "created_conversations": created,
        "updated_conversations": updated,
        "removed_conversations": len(obsolete_ids),
        "conversation_ids": conversation_ids,
    }


def relink_candidate_conversation(
    *,
    company_id: int,
    candidate_id: int,
    conversation_id: int | None,
    batch_id: int | None,
) -> None:
    """Reaponta um candidato para a conversa atual do lote."""
    resolved_company_id = require_company_id(company_id, context="candidate relink")
    execute(
        """
        UPDATE training_candidates
        SET conversation_id = ?,
            batch_id = COALESCE(?, batch_id),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (conversation_id, batch_id, candidate_id, resolved_company_id),
    )


def get_staged_conversations(*, company_id: int, batch_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="import staging read")
    return fetch_all(
        """
        SELECT
            id,
            company_id,
            batch_id,
            external_conversation_id,
            contact_ref_hash,
            session_index,
            started_at,
            ended_at,
            message_count,
            status,
            created_at
        FROM imported_conversations
        WHERE company_id = ?
          AND batch_id = ?
        ORDER BY id ASC
        """,
        (resolved_company_id, batch_id),
    )


def get_staged_messages(*, company_id: int, conversation_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="import staging read")
    return fetch_all(
        """
        SELECT
            id,
            company_id,
            batch_id,
            conversation_id,
            sequence,
            role,
            anonymized_content,
            pii_report_json,
            occurred_at,
            source_line
        FROM imported_messages
        WHERE company_id = ?
          AND conversation_id = ?
        ORDER BY sequence ASC
        """,
        (resolved_company_id, conversation_id),
    )


def upsert_candidate(*, company_id: int, candidate: dict[str, Any]) -> tuple[int, bool]:
    """Cria um candidato. Idempotente por `candidate_hash`.

    Devolve `(id, created)`. Um candidato já revisado nunca é sobrescrito: a
    reimportação apenas reaponta o vínculo com a conversa atual, preservando a
    decisão humana.
    """
    resolved_company_id = require_company_id(company_id, context="training candidate write")
    existing = fetch_one(
        "SELECT id, review_status FROM training_candidates WHERE company_id = ? AND candidate_hash = ?",
        (resolved_company_id, candidate["candidate_hash"]),
    )
    if existing:
        relink_candidate_conversation(
            company_id=resolved_company_id,
            candidate_id=int(existing["id"]),
            conversation_id=candidate.get("conversation_id"),
            batch_id=candidate.get("batch_id"),
        )
        return int(existing["id"]), False

    candidate_id = execute(
        """
        INSERT INTO training_candidates (
            company_id,
            batch_id,
            conversation_id,
            candidate_hash,
            group_key,
            scenario_type,
            customer_profile,
            profile_name,
            context_json,
            customer_message,
            approved_reply,
            quality_score,
            pii_report_json,
            review_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            candidate.get("batch_id"),
            candidate.get("conversation_id"),
            candidate["candidate_hash"],
            candidate["group_key"],
            candidate["scenario_type"],
            candidate.get("customer_profile"),
            candidate.get("profile_name"),
            json.dumps(candidate.get("context") or [], ensure_ascii=False),
            candidate["customer_message"],
            candidate["approved_reply"],
            float(candidate.get("quality_score") or 0),
            json.dumps(candidate.get("pii_report") or {}, ensure_ascii=False),
            REVIEW_PENDING,
        ),
    )
    return candidate_id, True


def list_candidates(
    *,
    company_id: int,
    review_status: str | None = None,
    batch_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="training candidate listing")
    review_condition, review_params = optional_equals("review_status", review_status)
    batch_condition, batch_params = optional_equals("batch_id", batch_id)
    return fetch_all(
        f"""
        SELECT {_CANDIDATE_COLUMNS}
        FROM training_candidates
        WHERE company_id = ?
          AND {review_condition}
          AND {batch_condition}
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, *review_params, *batch_params, limit),
    )


def get_candidate(*, company_id: int, candidate_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="training candidate read")
    return fetch_one(
        f"SELECT {_CANDIDATE_COLUMNS} FROM training_candidates WHERE id = ? AND company_id = ?",
        (candidate_id, resolved_company_id),
    )


def set_candidate_review(
    *,
    company_id: int,
    candidate_id: int,
    review_status: str,
    reviewed_by: str | None,
    review_notes: str | None = None,
    promoted_message_example_id: int | None = None,
    promotion_state: str | None = None,
) -> bool:
    resolved_company_id = require_company_id(company_id, context="training candidate review")
    return (
        execute_rowcount(
            """
            UPDATE training_candidates
            SET review_status = ?,
                reviewed_by = ?,
                review_notes = ?,
                promoted_message_example_id = COALESCE(?, promoted_message_example_id),
                promotion_state = COALESCE(?, promotion_state),
                reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (
                review_status,
                reviewed_by,
                review_notes,
                promoted_message_example_id,
                promotion_state,
                candidate_id,
                resolved_company_id,
            ),
        )
        > 0
    )


def count_candidates_by_status(*, company_id: int) -> dict[str, int]:
    resolved_company_id = require_company_id(company_id, context="training candidate stats")
    rows = fetch_all(
        """
        SELECT review_status, COUNT(*) AS total
        FROM training_candidates
        WHERE company_id = ?
        GROUP BY review_status
        """,
        (resolved_company_id,),
    )
    return {str(row["review_status"]): int(row["total"]) for row in rows}
