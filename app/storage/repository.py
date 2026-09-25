import functools
import hmac
import inspect
import json
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.db import (
    connection_scope,
    db_session,
    db_transaction,
    get_connection,
    get_database_backend,
    is_unique_violation,
)
from app.storage.migrations_booking_capacity import (
    DEFAULT_RESOURCE_KEY,
    DEFAULT_RESOURCE_LABEL,
    RESOURCE_KEY_PATTERN,
    BookingCapacityConflictError,
    BookingResourceUnavailableError,
    is_booking_capacity_violation,
)

PLACEHOLDER_COMPANY_SLUGS = {
    "string",
    "empresa-teste",
    "empresa_teste",
    "demo",
    "test",
    "teste",
}
PLACEHOLDER_COMPANY_NAMES = {
    "string",
    "empresa teste",
    "demo",
    "test company",
}

# Neutral condition used when an optional filter is not applied. PostgreSQL
# cannot infer the type of a bare placeholder in `? IS NULL OR col = ?` and
# raises IndeterminateDatatype, so the condition is built instead of bound.
# Partial unique index guaranteeing one active company per WhatsApp number.
WHATSAPP_ACTIVE_PHONE_INDEX = "idx_whatsapp_bindings_active_phone"
WHATSAPP_ACTIVE_PHONE_COLUMN = "whatsapp_channel_bindings.phone_number_id"


def _assert_sql_identifier(column: str) -> str:
    """Reject anything that is not a bare column identifier before interpolation."""
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(column):
        raise ValueError(f"Unsafe SQL identifier: {column!r}")
    return column


def _is_active_phone_number_conflict(error: BaseException) -> bool:
    """Whether the error is the active-phone-number uniqueness violation."""
    return is_unique_violation(
        error,
        postgres_constraint=WHATSAPP_ACTIVE_PHONE_INDEX,
        sqlite_qualified_column=WHATSAPP_ACTIVE_PHONE_COLUMN,
    )


ALWAYS_TRUE_CONDITION = "1 = 1"
# Bare column, or `alias.column`. Anything else is rejected outright.
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _iso_timestamp(moment: datetime) -> str:
    """Fixed-width UTC text for SQLite ordering and PostgreSQL parity."""
    normalized = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds")


def _optional_equals(column: str, value: Any) -> tuple[str, tuple[Any, ...]]:
    """Build a parameterized equality condition for an optional filter.

    ``value is None`` means "do not filter" and yields the neutral condition
    with no bound parameters. Values are *always* bound as parameters; only the
    column identifier is interpolated, and it is validated against a strict
    identifier pattern so a future caller cannot turn this into an injection
    point even by mistake.
    """
    if not _SQL_IDENTIFIER_PATTERN.match(column):
        raise ValueError(f"Unsafe SQL identifier for optional filter: {column!r}")
    if value is None:
        return ALWAYS_TRUE_CONDITION, ()
    return f"{column} = ?", (value,)


class RuntimeRepository:
    def get_state(self, company_id: int | None = None) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()
        condition, params = _optional_equals("company_id", company_id)

        cursor.execute(f"SELECT COUNT(*) AS count FROM messages WHERE {condition}", params)
        messages_count = cursor.fetchone()["count"]

        cursor.execute(
            f"SELECT COUNT(*) AS count FROM contact_company_profiles WHERE {condition}",
            params,
        )
        contacts_count = cursor.fetchone()["count"]

        connection.close()

        return {
            "messages_processed": messages_count,
            "contacts_count": contacts_count,
        }

    def get_observability_summary(self, company_id: int | None = None) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()
        condition, company_params = _optional_equals("company_id", company_id)

        def fetch_count(query: str, params: tuple[Any, ...] = ()) -> int:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row["count"]) if row else 0

        def fetch_grouped_counts(
            query: str,
            params: tuple[Any, ...] = (),
        ) -> dict[str, int]:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            result: dict[str, int] = {}
            for row in rows:
                group_key = row["group_key"] if row["group_key"] is not None else "unknown"
                result[str(group_key)] = int(row["count"])
            return result

        summary = {
            "totals": {
                "messages": fetch_count(f"SELECT COUNT(*) AS count FROM messages WHERE {condition}", company_params),
                "contacts": fetch_count(f"SELECT COUNT(*) AS count FROM contact_company_profiles WHERE {condition}", company_params),
                "pending_questions": fetch_count(f"SELECT COUNT(*) AS count FROM pending_questions WHERE {condition}", company_params),
                "knowledge_entries": fetch_count(f"SELECT COUNT(*) AS count FROM knowledge_entries WHERE {condition}", company_params),
                "message_examples": fetch_count(f"SELECT COUNT(*) AS count FROM message_examples WHERE {condition}", company_params),
                "outbound_messages": fetch_count(f"SELECT COUNT(*) AS count FROM outbound_messages WHERE {condition}", company_params),
                "finetuning_jobs": fetch_count(f"SELECT COUNT(*) AS count FROM finetuning_jobs WHERE {condition}", company_params),
                "companies": fetch_count(
                    "SELECT COUNT(*) AS count FROM companies"
                    if company_id is None
                    else "SELECT COUNT(*) AS count FROM companies WHERE id = ?",
                    () if company_id is None else (company_id,),
                ),
                "audit_events": fetch_count(f"SELECT COUNT(*) AS count FROM audit_events WHERE {condition}", company_params),
            },
            "pending_questions_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM pending_questions
                WHERE {condition}
                GROUP BY status
                """,
                company_params,
            ),
            "outbound_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM outbound_messages
                WHERE {condition}
                GROUP BY status
                """,
                company_params,
            ),
            "knowledge_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM knowledge_entries
                WHERE {condition}
                GROUP BY status
                """,
                company_params,
            ),
            "messages_by_classification": fetch_grouped_counts(
                f"""
                SELECT classification AS group_key, COUNT(*) AS count
                FROM messages
                WHERE {condition}
                GROUP BY classification
                """,
                company_params,
            ),
            "messages_by_action": fetch_grouped_counts(
                f"""
                SELECT action AS group_key, COUNT(*) AS count
                FROM messages
                WHERE {condition}
                GROUP BY action
                """,
                company_params,
            ),
            "messages_by_reply_source": fetch_grouped_counts(
                f"""
                SELECT reply_source AS group_key, COUNT(*) AS count
                FROM messages
                WHERE {condition}
                GROUP BY reply_source
                """,
                company_params,
            ),
            "companies_by_status": {
                "active": fetch_count(
                    "SELECT COUNT(*) AS count FROM companies WHERE active = 1"
                    + (" AND id = ?" if company_id is not None else ""),
                    () if company_id is None else (company_id,),
                ),
                "inactive": fetch_count(
                    "SELECT COUNT(*) AS count FROM companies WHERE active = 0"
                    + (" AND id = ?" if company_id is not None else ""),
                    () if company_id is None else (company_id,),
                ),
            },
            "audit_events_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM audit_events
                WHERE {condition}
                GROUP BY status
                """,
                company_params,
            ),
        }

        connection.close()
        return summary

    def get_operational_snapshot(self, company_id: int | None = None) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        def fetch_count(query: str, params: tuple[Any, ...] | None = None) -> int:
            cursor.execute(query, params or ())
            row = cursor.fetchone()
            return int(row["count"]) if row else 0

        def fetch_grouped_counts(query: str, params: tuple[Any, ...] | None = None) -> dict[str, int]:
            cursor.execute(query, params or ())
            rows = cursor.fetchall()
            result: dict[str, int] = {}
            for row in rows:
                group_key = row["group_key"] if row["group_key"] is not None else "unknown"
                result[str(group_key)] = int(row["count"])
            return result

        base_filter = "" if company_id is None else " WHERE company_id = ?"
        company_params = () if company_id is None else (company_id,)

        snapshot = {
            "company_id": company_id,
            "counts": {
                "contacts": fetch_count(
                    f"SELECT COUNT(*) AS count FROM contact_company_profiles{base_filter}",
                    company_params,
                ),
                "messages": fetch_count(
                    f"SELECT COUNT(*) AS count FROM messages{base_filter}",
                    company_params,
                ),
                "pending_questions": fetch_count(
                    f"SELECT COUNT(*) AS count FROM pending_questions{base_filter}",
                    company_params,
                ),
                "knowledge_entries": fetch_count(
                    f"SELECT COUNT(*) AS count FROM knowledge_entries{base_filter}",
                    company_params,
                ),
                "message_examples": fetch_count(
                    f"SELECT COUNT(*) AS count FROM message_examples{base_filter}",
                    company_params,
                ),
                "outbound_messages": fetch_count(
                    f"SELECT COUNT(*) AS count FROM outbound_messages{base_filter}",
                    company_params,
                ),
                "finetuning_jobs": fetch_count(
                    f"SELECT COUNT(*) AS count FROM finetuning_jobs{base_filter}",
                    company_params,
                ),
            },
            "pending_questions_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM pending_questions{base_filter}
                GROUP BY status
                """,
                company_params,
            ),
            "outbound_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM outbound_messages{base_filter}
                GROUP BY status
                """,
                company_params,
            ),
            "knowledge_by_status": fetch_grouped_counts(
                f"""
                SELECT status AS group_key, COUNT(*) AS count
                FROM knowledge_entries{base_filter}
                GROUP BY status
                """,
                company_params,
            ),
        }

        connection.close()
        return snapshot

    def create_audit_event(
        self,
        actor_id: str,
        action: str,
        resource_type: str,
        admin_role: str | None = None,
        auth_mode: str | None = None,
        resource_id: str | None = None,
        company_id: int | None = None,
        status: str = "success",
        request_id: str | None = None,
        http_method: str | None = None,
        http_path: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO audit_events (
                actor_id,
                admin_role,
                auth_mode,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
                http_method,
                http_path,
                client_ip,
                user_agent,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                admin_role,
                auth_mode,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
                http_method,
                http_path,
                client_ip,
                user_agent,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

        audit_event_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(audit_event_id)

    def get_audit_events(
        self,
        limit: int = 100,
        action: str | None = None,
        resource_type: str | None = None,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        action_condition, action_params = _optional_equals("action", action)
        resource_type_condition, resource_type_params = _optional_equals("resource_type", resource_type)
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        query = f"""
            SELECT
                id,
                actor_id,
                admin_role,
                auth_mode,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
                http_method,
                http_path,
                client_ip,
                user_agent,
                metadata_json,
                created_at
            FROM audit_events
            WHERE {action_condition}
              AND {resource_type_condition}
              AND {company_id_condition}
            ORDER BY id DESC
            LIMIT ?
        """
        cursor.execute(
            query,
            (*action_params, *resource_type_params, *company_id_params, limit),
        )

        rows = cursor.fetchall()
        connection.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            metadata_json = item.get("metadata_json")
            try:
                item["metadata"] = json.loads(metadata_json) if metadata_json else {}
            except json.JSONDecodeError:
                item["metadata"] = {"raw": metadata_json}
            item.pop("metadata_json", None)
            result.append(item)

        return result

    def get_operational_hygiene_summary(self, stale_after_days: int = 30) -> dict[str, Any]:
        normalized_days = max(1, int(stale_after_days))
        retention_modifier = f"-{normalized_days} days"
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, slug, name, business_type, active, created_at, updated_at
            FROM companies
            ORDER BY id ASC
            """
        )
        company_rows = [dict(row) for row in cursor.fetchall()]

        placeholder_companies: list[dict[str, Any]] = []
        for company in company_rows:
            slug = str(company.get("slug") or "").strip().lower()
            name = str(company.get("name") or "").strip().lower()
            is_placeholder = (
                slug in PLACEHOLDER_COMPANY_SLUGS or name in PLACEHOLDER_COMPANY_NAMES or slug.startswith(("demo-", "test-"))
            )
            if not is_placeholder or not company.get("active"):
                continue
            snapshot = self.get_operational_snapshot(company_id=int(company["id"]))
            placeholder_companies.append(
                {
                    **company,
                    "operational_counts": snapshot["counts"],
                }
            )

        cursor.execute(
            """
            SELECT id, contact_id, company_id, created_at
            FROM pending_questions
            WHERE status = 'pending'
              AND created_at < datetime('now', ?)
            ORDER BY created_at ASC, id ASC
            """,
            (retention_modifier,),
        )
        stale_pending_questions = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT id, contact_id, company_id, source, created_at
            FROM outbound_messages
            WHERE status = 'pending'
              AND created_at < datetime('now', ?)
            ORDER BY created_at ASC, id ASC
            """,
            (retention_modifier,),
        )
        stale_outbound_messages = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT id, job_id, company_id, status, created_at, finished_at
            FROM finetuning_jobs
            WHERE status = 'failed'
              AND created_at < datetime('now', ?)
            ORDER BY created_at ASC, id ASC
            """,
            (retention_modifier,),
        )
        stale_failed_finetuning_jobs = [dict(row) for row in cursor.fetchall()]

        connection.close()
        return {
            "stale_after_days": normalized_days,
            "counts": {
                "active_placeholder_companies": len(placeholder_companies),
                "stale_pending_questions": len(stale_pending_questions),
                "stale_outbound_messages": len(stale_outbound_messages),
                "stale_failed_finetuning_jobs": len(stale_failed_finetuning_jobs),
            },
            "active_placeholder_companies": placeholder_companies,
            "stale_pending_questions": stale_pending_questions,
            "stale_outbound_messages": stale_outbound_messages,
            "stale_failed_finetuning_jobs": stale_failed_finetuning_jobs,
        }

    def deactivate_companies(self, company_ids: list[int]) -> int:
        normalized_ids = [int(company_id) for company_id in company_ids if int(company_id) > 0]
        if not normalized_ids:
            return 0

        connection = get_connection()
        cursor = connection.cursor()
        updated_count = 0
        for company_id in normalized_ids:
            cursor.execute(
                """
                UPDATE companies
                SET
                    active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND active != 0
                """,
                (company_id,),
            )
            updated_count += max(0, int(cursor.rowcount))

        connection.commit()
        connection.close()
        return updated_count

    def archive_pending_questions(
        self,
        question_ids: list[int],
        *,
        owner_answer: str = "Archived by operational cleanup.",
        resolution_type: str = "cleanup_stale_pending",
        company_id: int | None = None,
    ) -> int:
        normalized_ids = [int(question_id) for question_id in question_ids if int(question_id) > 0]
        if not normalized_ids:
            return 0

        connection = get_connection()
        cursor = connection.cursor()
        updated_count = 0
        for question_id in normalized_ids:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE pending_questions
                SET
                    status = 'expired',
                    owner_answer = ?,
                    resolution_type = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                  AND {company_id_condition}
                """,
                (owner_answer, resolution_type, question_id, *company_id_params),
            )
            updated_count += max(0, int(cursor.rowcount))

        connection.commit()
        connection.close()
        return updated_count

    def cancel_outbound_messages(
        self,
        outbound_message_ids: list[int],
        *,
        cancelled_status: str = "cancelled",
        company_id: int | None = None,
    ) -> int:
        normalized_ids = [int(message_id) for message_id in outbound_message_ids if int(message_id) > 0]
        if not normalized_ids:
            return 0

        connection = get_connection()
        cursor = connection.cursor()
        updated_count = 0
        for outbound_message_id in normalized_ids:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE outbound_messages
                SET status = ?
                WHERE id = ?
                  AND status = 'pending'
                  AND {company_id_condition}
                """,
                (cancelled_status, outbound_message_id, *company_id_params),
            )
            updated_count += max(0, int(cursor.rowcount))

        connection.commit()
        connection.close()
        return updated_count

    def archive_finetuning_jobs(
        self,
        finetuning_job_ids: list[int],
        *,
        archived_status: str = "archived_failed",
        company_id: int | None = None,
    ) -> int:
        normalized_ids = [int(job_id) for job_id in finetuning_job_ids if int(job_id) > 0]
        if not normalized_ids:
            return 0

        connection = get_connection()
        cursor = connection.cursor()
        updated_count = 0
        for finetuning_job_id in normalized_ids:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE finetuning_jobs
                SET status = ?
                WHERE id = ?
                  AND status = 'failed'
                  AND {company_id_condition}
                """,
                (archived_status, finetuning_job_id, *company_id_params),
            )
            updated_count += max(0, int(cursor.rowcount))

        connection.commit()
        connection.close()
        return updated_count

    def purge_audit_events_older_than(self, older_than_days: int) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        retention_modifier = f"-{older_than_days} days"
        cursor.execute(
            """
            DELETE FROM audit_events
            WHERE created_at < datetime('now', ?)
            """,
            (retention_modifier,),
        )
        deleted_rows = cursor.rowcount
        connection.commit()
        connection.close()

        return int(deleted_rows)

    def get_schema_migrations(self) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT version, name, applied_at
            FROM schema_migrations
            ORDER BY version ASC
            """
        )
        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def save_contact(
        self,
        contact_id: str,
        contact_name: str | None,
        company_id: int | None = None,
        customer_profile: str | None = None,
        notes: str | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT OR IGNORE INTO contacts (contact_id, contact_name, company_id)
            VALUES (?, ?, ?)
            """,
            (contact_id, contact_name, None),
        )

        if contact_name:
            cursor.execute(
                """
                UPDATE contacts
                SET
                    contact_name = COALESCE(?, contact_name)
                WHERE contact_id = ?
                """,
                (contact_name, contact_id),
            )

        if company_id is not None:
            cursor.execute(
                """
                SELECT 1
                FROM contact_company_profiles
                WHERE contact_id = ?
                  AND company_id = ?
                """,
                (contact_id, company_id),
            )
            exists = cursor.fetchone() is not None

            if exists:
                cursor.execute(
                    """
                    UPDATE contact_company_profiles
                    SET
                        contact_name = COALESCE(?, contact_name),
                        customer_profile = COALESCE(?, customer_profile),
                        notes = COALESCE(?, notes),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE contact_id = ?
                      AND company_id = ?
                    """,
                    (contact_name, customer_profile, notes, contact_id, company_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO contact_company_profiles (
                        contact_id,
                        company_id,
                        contact_name,
                        customer_profile,
                        notes,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (contact_id, company_id, contact_name, customer_profile, notes),
                )

        connection.commit()
        connection.close()

    def save_message(
        self,
        contact_id: str,
        message: str,
        classification: str,
        reply: str,
        classification_reason: str | None = None,
        classification_confidence: float | None = None,
        reply_source: str | None = None,
        action: str | None = None,
        priority: str | None = None,
        auto_reply: bool = False,
        company_id: int | None = None,
        automation_event_factory: Callable[[int], dict[str, Any] | None] | None = None,
    ) -> int | None:
        """Persiste a mensagem e, opcionalmente, o evento de automação.

        `automation_event_factory` recebe o id da mensagem recém-criada e
        devolve o evento canônico a gravar. Evento e mensagem entram no
        **mesmo commit**: é isso que torna o outbox transacional, e o que
        elimina a janela em que a mensagem está salva e o fato para a
        automação foi perdido.

        A escrita do evento acontece sob SAVEPOINT dentro de
        `insert_event_with_outbox_on_cursor`, então uma falha ali desfaz só o
        evento. Automação é pós-atendimento: ela nunca derruba a conversa.
        """
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO messages (
                contact_id,
                company_id,
                message,
                classification,
                reply,
                classification_reason,
                classification_confidence,
                auto_reply,
                reply_source,
                action,
                priority
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contact_id,
                company_id,
                message,
                classification,
                reply,
                classification_reason,
                classification_confidence,
                int(auto_reply),
                reply_source,
                action,
                priority,
            ),
        )

        message_id = cursor.lastrowid
        if automation_event_factory is not None and message_id is not None:
            self._write_automation_event(
                cursor,
                company_id=company_id,
                event_factory=automation_event_factory,
                message_id=int(message_id),
            )

        connection.commit()
        connection.close()
        return int(message_id) if message_id is not None else None

    @staticmethod
    def _write_automation_event(
        cursor: Any,
        *,
        company_id: int | None,
        event_factory: Callable[[int], dict[str, Any] | None],
        message_id: int,
    ) -> None:
        """Grava o evento de automação no cursor da transação em curso.

        A **construção** do evento acontece dentro do try, e não no chamador.
        Construir fora deixaria a exceção da factory escapar por cima desta
        guarda e derrubaria o atendimento — que é exatamente o modo de falha
        que o `except Exception` aqui existe para impedir. Automação é
        pós-atendimento: ela nunca decide se a conversa acontece.

        Import tardio para evitar o ciclo automation_repository -> repository.
        """
        if company_id is None:
            return
        try:
            from app.storage.automation_repository import insert_event_with_outbox_on_cursor

            event = event_factory(message_id)
            if event is None:
                return
            insert_event_with_outbox_on_cursor(
                cursor,
                company_id=int(company_id),
                event_key=str(event["event_key"]),
                event_type=str(event["event_type"]),
                schema_version=str(event["schema_version"]),
                payload=dict(event["payload"]),
            )
        except Exception:
            # Import tardio como no resto deste módulo: app.core.logging só é
            # necessário no caminho de falha, e importá-lo no topo fecharia o
            # ciclo com app.storage.db.
            from app.core.logging import get_logger

            get_logger(__name__).warning("automation.event.write_failed", exc_info=True)

    def get_messages(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                message,
                classification,
                classification_reason,
                classification_confidence,
                reply,
                auto_reply,
                created_at,
                reply_source,
                action,
                priority
            FROM messages
            WHERE {company_id_condition}
            ORDER BY id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_latest_message_with_reply_source(
        self,
        contact_id: str,
        *,
        company_id: int,
        reply_source: str,
    ) -> dict[str, Any] | None:
        """Última mensagem do contato respondida por uma origem específica.

        Usada para não repetir avisos automáticos (ex.: fora do horário) a
        cada nova mensagem do mesmo cliente.
        """
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, contact_id, company_id, reply_source, action, created_at
            FROM messages
            WHERE company_id = ?
              AND contact_id = ?
              AND reply_source = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (company_id, contact_id, reply_source),
        )
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_contact(
        self,
        contact_id: str,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is not None:
            cursor.execute(
                """
                SELECT
                    contact_id,
                    contact_name,
                    company_id,
                    last_classification,
                    last_interaction_at,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    customer_profile,
                    notes,
                    updated_at
                FROM contact_company_profiles
                WHERE contact_id = ?
                  AND company_id = ?
                """,
                (contact_id, company_id),
            )
            row = cursor.fetchone()
            connection.close()
            return dict(row) if row else None

        cursor.execute(
            """
            SELECT
                contact_id,
                contact_name,
                company_id,
                last_classification,
                last_interaction_at,
                conversation_summary,
                pipeline_stage,
                next_action,
                lead_temperature,
                customer_profile,
                notes,
                updated_at
            FROM contact_company_profiles
            WHERE contact_id = ?
            ORDER BY updated_at DESC, company_id DESC
            """,
            (contact_id,),
        )
        profile_rows = cursor.fetchall()
        if len(profile_rows) == 1:
            connection.close()
            return dict(profile_rows[0])

        cursor.execute(
            """
            SELECT
                contact_id,
                contact_name,
                NULL AS company_id,
                NULL AS last_classification,
                NULL AS last_interaction_at,
                NULL AS conversation_summary,
                NULL AS pipeline_stage,
                NULL AS next_action,
                NULL AS lead_temperature,
                NULL AS customer_profile,
                NULL AS notes,
                NULL AS updated_at
            FROM contacts
            WHERE contact_id = ?
            """,
            (contact_id,),
        )
        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def update_contact_memory(
        self,
        contact_id: str,
        last_classification: str,
        conversation_summary: str,
        pipeline_stage: str | None = None,
        next_action: str | None = None,
        lead_temperature: str | None = None,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                UPDATE contacts
                SET
                    last_classification = ?,
                    last_interaction_at = CURRENT_TIMESTAMP,
                    conversation_summary = ?,
                    pipeline_stage = ?,
                    next_action = ?,
                    lead_temperature = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE contact_id = ?
                """,
                (
                    last_classification,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    contact_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE contact_company_profiles
                SET
                    last_classification = ?,
                    last_interaction_at = CURRENT_TIMESTAMP,
                    conversation_summary = ?,
                    pipeline_stage = ?,
                    next_action = ?,
                    lead_temperature = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE contact_id = ?
                  AND company_id = ?
                """,
                (
                    last_classification,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    contact_id,
                    company_id,
                ),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO contacts (contact_id, contact_name, company_id)
                    VALUES (?, NULL, NULL)
                    """,
                    (contact_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO contact_company_profiles (
                        contact_id,
                        company_id,
                        last_classification,
                        last_interaction_at,
                        conversation_summary,
                        pipeline_stage,
                        next_action,
                        lead_temperature,
                        updated_at
                    )
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        contact_id,
                        company_id,
                        last_classification,
                        conversation_summary,
                        pipeline_stage,
                        next_action,
                        lead_temperature,
                    ),
                )

        connection.commit()
        connection.close()

    def get_contacts(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    contact_id,
                    contact_name,
                    company_id,
                    last_classification,
                    last_interaction_at,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    customer_profile,
                    notes,
                    updated_at
                FROM contact_company_profiles
                ORDER BY company_id, contact_id
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    contact_id,
                    contact_name,
                    company_id,
                    last_classification,
                    last_interaction_at,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    customer_profile,
                    notes,
                    updated_at
                FROM contact_company_profiles
                WHERE company_id = ?
                ORDER BY contact_id
                """,
                (company_id,),
            )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def search_contacts(
        self,
        *,
        query: str,
        company_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        connection = get_connection()
        cursor = connection.cursor()
        like_query = f"%{normalized_query.lower()}%"

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    contact_id,
                    contact_name,
                    company_id,
                    last_classification,
                    last_interaction_at,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    customer_profile,
                    notes,
                    updated_at
                FROM contact_company_profiles
                WHERE
                    LOWER(COALESCE(contact_name, '')) LIKE ?
                    OR LOWER(COALESCE(contact_id, '')) LIKE ?
                ORDER BY
                    CASE
                        WHEN LOWER(COALESCE(contact_name, '')) = ? THEN 0
                        WHEN LOWER(COALESCE(contact_id, '')) = ? THEN 0
                        WHEN LOWER(COALESCE(contact_name, '')) LIKE ? THEN 1
                        WHEN LOWER(COALESCE(contact_id, '')) LIKE ? THEN 1
                        ELSE 2
                    END,
                    updated_at DESC,
                    contact_name ASC,
                    contact_id ASC
                LIMIT ?
                """,
                (
                    like_query,
                    like_query,
                    normalized_query.lower(),
                    normalized_query.lower(),
                    f"{normalized_query.lower()}%",
                    f"{normalized_query.lower()}%",
                    max(1, limit),
                ),
            )
        else:
            cursor.execute(
                """
                SELECT
                    contact_id,
                    contact_name,
                    company_id,
                    last_classification,
                    last_interaction_at,
                    conversation_summary,
                    pipeline_stage,
                    next_action,
                    lead_temperature,
                    customer_profile,
                    notes,
                    updated_at
                FROM contact_company_profiles
                WHERE company_id = ?
                  AND (
                    LOWER(COALESCE(contact_name, '')) LIKE ?
                    OR LOWER(COALESCE(contact_id, '')) LIKE ?
                  )
                ORDER BY
                    CASE
                        WHEN LOWER(COALESCE(contact_name, '')) = ? THEN 0
                        WHEN LOWER(COALESCE(contact_id, '')) = ? THEN 0
                        WHEN LOWER(COALESCE(contact_name, '')) LIKE ? THEN 1
                        WHEN LOWER(COALESCE(contact_id, '')) LIKE ? THEN 1
                        ELSE 2
                    END,
                    updated_at DESC,
                    contact_name ASC,
                    contact_id ASC
                LIMIT ?
                """,
                (
                    company_id,
                    like_query,
                    like_query,
                    normalized_query.lower(),
                    normalized_query.lower(),
                    f"{normalized_query.lower()}%",
                    f"{normalized_query.lower()}%",
                    max(1, limit),
                ),
            )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def update_contact(
        self,
        contact_id: str,
        *,
        contact_name: str | None = None,
        company_id: int | None = None,
        customer_profile: str | None = None,
        notes: str | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        if contact_name:
            cursor.execute(
                """
                UPDATE contacts
                SET contact_name = COALESCE(?, contact_name)
                WHERE contact_id = ?
                """,
                (contact_name, contact_id),
            )

        if company_id is not None:
            cursor.execute(
                """
                UPDATE contact_company_profiles
                SET
                    contact_name = COALESCE(?, contact_name),
                    customer_profile = COALESCE(?, customer_profile),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE contact_id = ?
                  AND company_id = ?
                """,
                (contact_name, customer_profile, notes, contact_id, company_id),
            )

        connection.commit()
        connection.close()

    def create_pending_question(
        self,
        contact_id: str,
        customer_message: str,
        question_type: str,
        question_for_owner: str,
        company_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO pending_questions (
                contact_id,
                company_id,
                customer_message,
                question_type,
                question_for_owner,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (contact_id, company_id, customer_message, question_type, question_for_owner),
        )

        pending_question_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(pending_question_id)

    def has_open_pending_question(
        self,
        contact_id: str,
        *,
        company_id: int,
        question_type: str,
    ) -> bool:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM pending_questions
            WHERE company_id = ?
              AND contact_id = ?
              AND question_type = ?
              AND status = 'pending'
            LIMIT 1
            """,
            (company_id, contact_id, question_type),
        )
        row = cursor.fetchone()
        connection.close()
        return row is not None

    def get_pending_questions(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                customer_message,
                question_type,
                question_for_owner,
                status,
                owner_answer,
                resolution_type,
                customer_followup_question,
                clarification_consumed,
                applies_globally,
                knowledge_action,
                created_at,
                resolved_at
            FROM pending_questions
            WHERE {company_id_condition}
            ORDER BY id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_pending_question(
        self,
        question_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                customer_message,
                question_type,
                question_for_owner,
                status,
                owner_answer,
                resolution_type,
                customer_followup_question,
                clarification_consumed,
                applies_globally,
                knowledge_action,
                created_at,
                resolved_at
            FROM pending_questions
            WHERE id = ?
              AND {company_id_condition}
            """,
            (question_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def resolve_pending_question(
        self,
        question_id: int,
        owner_answer: str,
        resolution_type: str | None = None,
        customer_followup_question: str | None = None,
        applies_globally: bool = False,
        knowledge_action: str | None = None,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE pending_questions
            SET
                owner_answer = ?,
                resolution_type = ?,
                customer_followup_question = ?,
                applies_globally = ?,
                knowledge_action = ?,
                status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            (owner_answer, resolution_type, customer_followup_question, int(applies_globally), knowledge_action, question_id, *company_id_params),
        )

        connection.commit()
        connection.close()

    def create_knowledge_entry(
        self,
        title: str,
        content: str,
        knowledge_type: str,
        source_question_id: int | None = None,
        contact_id: str | None = None,
        applies_globally: bool = False,
        status: str = "active",
        company_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO knowledge_entries (
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                applies_globally,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                int(applies_globally),
                status,
            ),
        )

        knowledge_entry_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(knowledge_entry_id)

    def get_knowledge_entries(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                applies_globally,
                status,
                created_at
            FROM knowledge_entries
            WHERE {company_id_condition}
            ORDER BY id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_knowledge_entry(
        self,
        knowledge_entry_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                applies_globally,
                status,
                created_at
            FROM knowledge_entries
            WHERE id = ?
              AND {company_id_condition}
            """,
            (knowledge_entry_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_active_knowledge_entries(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                applies_globally,
                status,
                created_at
            FROM knowledge_entries
            WHERE status = 'active'
              AND {company_id_condition}
            ORDER BY applies_globally DESC, id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_relevant_knowledge_entries(
        self,
        contact_id: str | None = None,
        limit: int = 10,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Conhecimento aplicável à empresa, opcionalmente restrito a um contato.

        `company_id` é obrigatório. Era opcional, e o valor ausente virava
        "sem filtro" — de modo que um chamador que esquecesse o parâmetro
        recebia o conhecimento de todas as empresas sem nenhum sinal de erro.

        O filtro de empresa envolve o predicado inteiro. Antes o ramo com
        contato era `(applies_globally = 1 AND company_id = ?) OR contact_id = ?`,
        e o `OR` escapava do escopo: `contact_id` é o telefone do cliente, o
        mesmo telefone pode ser contato de duas empresas, e o conhecimento
        registrado por uma aparecia para a outra.
        """
        resolved_company_id = require_company_id(
            company_id, context="knowledge entry retrieval"
        )
        connection = get_connection()
        cursor = connection.cursor()

        # Dentro da empresa: o que vale para todos os contatos, mais o que foi
        # registrado especificamente para este.
        contact_condition, contact_params = _optional_equals("contact_id", contact_id)
        scope_predicate = (
            f"(applies_globally = 1 OR {contact_condition})"
            if contact_id
            else "applies_globally = 1"
        )

        cursor.execute(
            f"""
            SELECT
                id,
                source_question_id,
                contact_id,
                company_id,
                title,
                content,
                knowledge_type,
                applies_globally,
                status,
                created_at
            FROM knowledge_entries
            WHERE status = 'active'
              AND company_id = ?
              AND {scope_predicate}
            ORDER BY applies_globally DESC, id DESC
            LIMIT ?
            """,
            (resolved_company_id, *contact_params, limit),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def create_message_example(
        self,
        profile_name: str,
        scenario_type: str,
        customer_message: str,
        approved_reply: str,
        style_notes: str | None = None,
        quality_score: float = 0,
        used_for_training: bool = False,
        active: bool = True,
        company_id: int | None = None,
        customer_profile: str | None = None,
        approval_status: str = "approved",
        # `source_type` é NOT NULL com default 'manual' no esquema. Passar
        # None explicitamente anula o default do banco e viola a constraint,
        # então o default vive aqui também.
        source_type: str = "manual",
        source_reference: str | None = None,
        source_group_key: str | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        source_batch_id: int | None = None,
        source_conversation_id: int | None = None,
        source_candidate_id: int | None = None,
        approved_by: str | None = None,
        pii_scrubbed: bool = False,
    ) -> int:
        """Cria um exemplo de estilo, com linhagem até a conversa de origem.

        Os campos de linhagem (migrations 049 e 053) existem para que um
        exemplo aprovado possa ser rastreado de volta ao lote, à conversa e ao
        candidato que o originou. Sem isso, descobrir que uma conversa foi
        importada errada não permite achar o que ela contaminou no dataset.

        `source_group_key` é o agrupamento estruturado. Antes esse papel era
        cumprido por `source_reference`, uma string sobrecarregada que servia
        de identificador e de agrupador ao mesmo tempo.
        """
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO message_examples (
                company_id,
                profile_name,
                scenario_type,
                customer_profile,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active,
                approval_status,
                source_type,
                source_reference,
                source_group_key,
                conversation_context_json,
                source_batch_id,
                source_conversation_id,
                source_candidate_id,
                approved_by,
                approved_at,
                pii_scrubbed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                profile_name,
                scenario_type,
                customer_profile,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                int(used_for_training),
                int(active),
                approval_status,
                source_type,
                source_reference,
                source_group_key,
                json.dumps(conversation_context or [], ensure_ascii=False),
                source_batch_id,
                source_conversation_id,
                source_candidate_id,
                approved_by,
                _iso_timestamp(datetime.now(UTC)) if approved_by else None,
                int(pii_scrubbed),
            ),
        )

        message_example_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(message_example_id)

    def get_message_example_by_candidate(
        self,
        *,
        company_id: int,
        source_candidate_id: int,
    ) -> dict[str, Any] | None:
        """O exemplo promovido a partir deste candidato, se já houver.

        Sustenta a idempotência da aprovação: aprovar o mesmo candidato duas
        vezes devolve o exemplo existente em vez de criar um segundo. O índice
        único `idx_message_examples_source_candidate` garante a invariante no
        banco; esta leitura evita chegar a violá-la.
        """
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, company_id, profile_name, scenario_type, customer_profile,
                   customer_message, approved_reply, style_notes, quality_score,
                   used_for_training, active, approval_status, source_candidate_id,
                   created_at
            FROM message_examples
            WHERE company_id = ? AND source_candidate_id = ?
            LIMIT 1
            """,
            (int(company_id), int(source_candidate_id)),
        )
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_message_examples(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                profile_name,
                scenario_type,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active,
                created_at
            FROM message_examples
            WHERE {company_id_condition}
            ORDER BY id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_message_example(
        self,
        message_example_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                profile_name,
                scenario_type,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active,
                created_at
            FROM message_examples
            WHERE id = ?
              AND {company_id_condition}
            """,
            (message_example_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_relevant_message_examples(
        self,
        profile_name: str | None = None,
        scenario_type: str | None = None,
        limit: int = 5,
        company_id: int | None = None,
        customer_profile: str | None = None,
    ) -> list[dict[str, Any]]:
        """Exemplos de estilo relevantes, filtrados pelas dimensões informadas.

        Era uma cadeia de quatro ramos com a mesma query repetida, uma por
        combinação de filtros — e acrescentar `customer_profile` teria dobrado
        os ramos para oito. Cada filtro opcional agora vira uma condição
        neutra quando ausente, via `_optional_equals`, então a query é uma só
        e uma dimensão nova custa uma linha.

        `approval_status` entra no filtro porque exemplo reprovado não deve
        virar few-shot: a coluna nasceu com default `approved` na migration
        049, então linhas anteriores continuam elegíveis.
        """
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        profile_condition, profile_params = _optional_equals("profile_name", profile_name)
        scenario_condition, scenario_params = _optional_equals("scenario_type", scenario_type)
        customer_condition, customer_params = _optional_equals(
            "customer_profile", customer_profile
        )

        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                profile_name,
                scenario_type,
                customer_profile,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active,
                created_at
            FROM message_examples
            WHERE active = 1
              AND approval_status = 'approved'
              AND {company_id_condition}
              AND {profile_condition}
              AND {scenario_condition}
              AND {customer_condition}
            ORDER BY quality_score DESC, id DESC
            LIMIT ?
            """,
            (
                *company_id_params,
                *profile_params,
                *scenario_params,
                *customer_params,
                limit,
            ),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_exportable_message_examples(
        self,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                profile_name,
                scenario_type,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active,
                created_at
            FROM message_examples
            WHERE active = 1
              AND {company_id_condition}
              AND used_for_training = 1
            ORDER BY quality_score DESC, id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_latest_customer_clarification(
        self,
        contact_id: str,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                customer_message,
                question_type,
                question_for_owner,
                status,
                owner_answer,
                resolution_type,
                customer_followup_question,
                clarification_consumed,
                applies_globally,
                knowledge_action,
                created_at,
                resolved_at
            FROM pending_questions
            WHERE contact_id = ?
              AND {company_id_condition}
              AND status = 'resolved'
              AND resolution_type = 'needs_customer_clarification'
              AND customer_followup_question IS NOT NULL
              AND TRIM(customer_followup_question) != ''
              AND clarification_consumed = 0
            ORDER BY resolved_at DESC, id DESC
            LIMIT 1
            """,
            (contact_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def begin_inbound_message_processing(
        self,
        *,
        provider: str,
        provider_message_id: str,
        company_id: int | None = None,
        contact_id: str | None = None,
        payload_mode: str | None = None,
    ) -> str:
        normalized_provider = provider.strip().lower()
        normalized_message_id = provider_message_id.strip()
        if not normalized_provider or not normalized_message_id:
            raise ValueError("provider and provider_message_id must be non-empty strings.")

        connection = get_connection()
        cursor = connection.cursor()
        created = False

        if get_database_backend() == "postgres":
            cursor.execute(
                """
                INSERT INTO inbound_message_receipts (
                    provider,
                    provider_message_id,
                    company_id,
                    contact_id,
                    payload_mode,
                    status,
                    last_error
                )
                VALUES (?, ?, ?, ?, ?, 'processing', NULL)
                ON CONFLICT (provider, provider_message_id) DO NOTHING
                RETURNING id
                """,
                (
                    normalized_provider,
                    normalized_message_id,
                    company_id,
                    contact_id,
                    payload_mode,
                ),
            )
            created = cursor.fetchone() is not None
        else:
            cursor.execute(
                """
                INSERT OR IGNORE INTO inbound_message_receipts (
                    provider,
                    provider_message_id,
                    company_id,
                    contact_id,
                    payload_mode,
                    status,
                    last_error
                )
                VALUES (?, ?, ?, ?, ?, 'processing', NULL)
                """,
                (
                    normalized_provider,
                    normalized_message_id,
                    company_id,
                    contact_id,
                    payload_mode,
                ),
            )
            created = cursor.rowcount > 0

        if created:
            connection.commit()
            connection.close()
            return "started"

        cursor.execute(
            """
            SELECT status
            FROM inbound_message_receipts
            WHERE provider = ?
              AND provider_message_id = ?
            """,
            (normalized_provider, normalized_message_id),
        )
        row = cursor.fetchone()
        current_status = str(row["status"]) if row else "completed"

        if current_status == "failed":
            cursor.execute(
                """
                UPDATE inbound_message_receipts
                SET
                    company_id = COALESCE(?, company_id),
                    contact_id = COALESCE(?, contact_id),
                    payload_mode = COALESCE(?, payload_mode),
                    status = 'processing',
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = ?
                  AND provider_message_id = ?
                """,
                (
                    company_id,
                    contact_id,
                    payload_mode,
                    normalized_provider,
                    normalized_message_id,
                ),
            )
            connection.commit()
            connection.close()
            return "started"

        connection.commit()
        connection.close()
        return current_status

    def mark_inbound_message_processing_completed(
        self,
        *,
        provider: str,
        provider_message_id: str,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE inbound_message_receipts
            SET
                status = 'completed',
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = ?
              AND provider_message_id = ?
              AND {company_id_condition}
            """,
            (provider.strip().lower(), provider_message_id.strip(), *company_id_params),
        )
        connection.commit()
        connection.close()

    def mark_inbound_message_processing_failed(
        self,
        *,
        provider: str,
        provider_message_id: str,
        error: str,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE inbound_message_receipts
            SET
                status = 'failed',
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = ?
              AND provider_message_id = ?
              AND {company_id_condition}
            """,
            (error, provider.strip().lower(), provider_message_id.strip(), *company_id_params),
        )
        connection.commit()
        connection.close()

    def mark_clarification_consumed(
        self,
        question_id: int,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE pending_questions
            SET clarification_consumed = 1
            WHERE id = ?
              AND {company_id_condition}
            """,
            (question_id, *company_id_params),
        )

        connection.commit()
        connection.close()

    def create_outbound_message(
        self,
        contact_id: str,
        message: str,
        source: str,
        pending_question_id: int | None = None,
        status: str = "pending",
        company_id: int | None = None,
        template_parameters: list[str] | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO outbound_messages (
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                template_parameters_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                # Parâmetros do template usados só fora da janela de 24 h (071).
                json.dumps(template_parameters, ensure_ascii=False) if template_parameters is not None else None,
            ),
        )

        outbound_message_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(outbound_message_id)

    def get_outbound_messages(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                channel,
                attempt_count,
                max_attempts,
                next_attempt_at,
                claimed_at,
                claim_token,
                last_error,
                provider_message_id,
                provider_response_json,
                failed_at,
                created_at,
                sent_at,
                template_parameters_json,
                delivery_status,
                delivery_updated_at
            FROM outbound_messages
            WHERE {company_id_condition}
            ORDER BY id DESC
            """,
            (*company_id_params,),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_outbound_message(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                channel,
                attempt_count,
                max_attempts,
                next_attempt_at,
                claimed_at,
                claim_token,
                last_error,
                provider_message_id,
                provider_response_json,
                failed_at,
                created_at,
                sent_at,
                template_parameters_json,
                delivery_status,
                delivery_updated_at
            FROM outbound_messages
            WHERE id = ?
              AND {company_id_condition}
            """,
            (outbound_message_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def mark_outbound_message_sent(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
        claim_token: str | None = None,
        channel: str | None = None,
        provider_message_id: str | None = None,
        provider_response: dict[str, Any] | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        claim_condition, claim_params = _optional_equals("claim_token", claim_token)
        cursor.execute(
            f"""
            UPDATE outbound_messages
            SET
                status = 'sent',
                sent_at = CURRENT_TIMESTAMP,
                channel = COALESCE(channel, ?),
                provider_message_id = COALESCE(?, provider_message_id),
                provider_response_json = COALESCE(?, provider_response_json),
                last_error = NULL,
                next_attempt_at = NULL,
                claimed_at = NULL,
                claim_token = NULL,
                failed_at = NULL
            WHERE id = ?
              AND {company_id_condition}
              AND {claim_condition}
            """,
            (
                channel,
                provider_message_id,
                (
                    json.dumps(provider_response, ensure_ascii=False)
                    if provider_response is not None
                    else None
                ),
                outbound_message_id,
                *company_id_params,
                *claim_params,
            ),
        )

        connection.commit()
        connection.close()

    def claim_outbound_message(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
        channel: str | None = None,
        claim_ttl_seconds: int = 300,
        max_attempts: int = 5,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=max(30, claim_ttl_seconds))
        claim_token = secrets.token_urlsafe(24)
        company_condition, company_params = _optional_equals("company_id", company_id)
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                UPDATE outbound_messages
                SET status = 'processing',
                    channel = COALESCE(?, channel),
                    attempt_count = attempt_count + 1,
                    max_attempts = CASE
                        WHEN attempt_count = 0 OR max_attempts IS NULL OR max_attempts < 1 THEN ?
                        ELSE max_attempts
                    END,
                    claimed_at = ?,
                    claim_token = ?,
                    last_error = NULL
                WHERE id = ?
                  AND {company_condition}
                  AND status IN ('pending', 'retry')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND (claimed_at IS NULL OR claimed_at <= ?)
                """,
                (
                    channel,
                    max(1, max_attempts),
                    _iso_timestamp(now),
                    claim_token,
                    outbound_message_id,
                    *company_params,
                    _iso_timestamp(now),
                    _iso_timestamp(stale_before),
                ),
            )
            if cursor.rowcount != 1:
                return None
            cursor.execute(
                """
                SELECT *
                FROM outbound_messages
                WHERE id = ?
                  AND claim_token = ?
                """,
                (outbound_message_id, claim_token),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def mark_outbound_message_failed(
        self,
        outbound_message_id: int,
        *,
        claim_token: str,
        error: str,
        retry_at: str | None,
    ) -> str:
        status = "retry" if retry_at else "dead_letter"
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE outbound_messages
                SET status = ?,
                    last_error = ?,
                    next_attempt_at = ?,
                    failed_at = CASE WHEN ? = 'dead_letter' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    claimed_at = NULL,
                    claim_token = NULL
                WHERE id = ?
                  AND claim_token = ?
                  AND status = 'processing'
                """,
                (
                    status,
                    str(error)[:2000],
                    retry_at,
                    status,
                    outbound_message_id,
                    claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Outbound claim was lost before failure could be recorded.")
        return status

    def acquire_worker_lease(
        self,
        lease_name: str,
        owner_token: str,
        *,
        ttl_seconds: int = 300,
    ) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=max(30, ttl_seconds))
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO worker_leases (lease_name, owner_token, expires_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (lease_name)
                DO UPDATE SET
                    owner_token = excluded.owner_token,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                WHERE worker_leases.expires_at <= ?
                   OR worker_leases.owner_token = ?
                """,
                (
                    lease_name,
                    owner_token,
                    _iso_timestamp(expires_at),
                    _iso_timestamp(now),
                    owner_token,
                ),
            )
            return cursor.rowcount == 1

    def release_worker_lease(self, lease_name: str, owner_token: str) -> bool:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                DELETE FROM worker_leases
                WHERE lease_name = ?
                  AND owner_token = ?
                """,
                (lease_name, owner_token),
            )
            return cursor.rowcount == 1

    def create_finetuning_job_record(
        self,
        job_id: str,
        status: str,
        base_model: str | None = None,
        suffix: str | None = None,
        training_file_id: str | None = None,
        validation_file_id: str | None = None,
        fine_tuned_model: str | None = None,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        company_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO finetuning_jobs (
                job_id,
                company_id,
                status,
                base_model,
                suffix,
                training_file_id,
                validation_file_id,
                fine_tuned_model,
                provider_error_code,
                provider_error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                company_id,
                status,
                base_model,
                suffix,
                training_file_id,
                validation_file_id,
                fine_tuned_model,
                provider_error_code,
                provider_error_message,
            ),
        )

        record_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(record_id)

    def update_finetuning_job_record(
        self,
        job_id: str,
        status: str,
        fine_tuned_model: str | None = None,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        finished_at: str | None = None,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        if finished_at is None:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE finetuning_jobs
                SET
                    status = ?,
                    fine_tuned_model = ?,
                    provider_error_code = ?,
                    provider_error_message = ?
                WHERE job_id = ?
                  AND {company_id_condition}
                """,
                (status, fine_tuned_model, provider_error_code, provider_error_message, job_id, *company_id_params),
            )
        else:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE finetuning_jobs
                SET
                    status = ?,
                    fine_tuned_model = ?,
                    provider_error_code = ?,
                    provider_error_message = ?,
                    finished_at = ?
                WHERE job_id = ?
                  AND {company_id_condition}
                """,
                (status, fine_tuned_model, provider_error_code, provider_error_message, finished_at, job_id, *company_id_params),
            )

        connection.commit()
        connection.close()

    def get_finetuning_job_record(self, job_id: str, company_id: int | None = None) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    id,
                    job_id,
                    company_id,
                    status,
                    base_model,
                    suffix,
                    training_file_id,
                    validation_file_id,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    created_at,
                    finished_at
                FROM finetuning_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            )
        else:
            cursor.execute(
                """
                SELECT
                    id,
                    job_id,
                    company_id,
                    status,
                    base_model,
                    suffix,
                    training_file_id,
                    validation_file_id,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    created_at,
                    finished_at
                FROM finetuning_jobs
                WHERE job_id = ?
                  AND company_id = ?
                """,
                (job_id, company_id),
            )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_finetuning_job_records(self, company_id: int | None = None) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    id,
                    job_id,
                    company_id,
                    status,
                    base_model,
                    suffix,
                    training_file_id,
                    validation_file_id,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    created_at,
                    finished_at
                FROM finetuning_jobs
                ORDER BY id DESC
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    id,
                    job_id,
                    company_id,
                    status,
                    base_model,
                    suffix,
                    training_file_id,
                    validation_file_id,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    created_at,
                    finished_at
                FROM finetuning_jobs
                WHERE company_id = ?
                ORDER BY id DESC
                """,
                (company_id,),
            )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def create_company(
        self,
        slug: str,
        name: str,
        business_type: str | None = None,
        active: bool = True,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO companies (
                slug,
                name,
                business_type,
                active,
                updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                slug,
                name,
                business_type,
                int(active),
            ),
        )

        company_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(company_id)

    def get_companies(self) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                slug,
                name,
                business_type,
                active,
                created_at,
                updated_at
            FROM companies
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_company(self, company_id: int) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                slug,
                name,
                business_type,
                active,
                created_at,
                updated_at
            FROM companies
            WHERE id = ?
            """,
            (company_id,),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_company_by_slug(self, slug: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                slug,
                name,
                business_type,
                active,
                created_at,
                updated_at
            FROM companies
            WHERE slug = ?
            """,
            (slug,),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_default_company(self) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                slug,
                name,
                business_type,
                active,
                created_at,
                updated_at
            FROM companies
            WHERE active = 1
            ORDER BY id ASC
            LIMIT 1
            """
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def resolve_company_id(
        self,
        company_id: int | None = None,
        company_slug: str | None = None,
    ) -> int | None:
        if company_id is not None:
            company = self.get_company(company_id)
            return int(company["id"]) if company else None

        if company_slug:
            company = self.get_company_by_slug(company_slug)
            return int(company["id"]) if company else None

        default_company = self.get_default_company()
        return int(default_company["id"]) if default_company else None

    def ensure_default_company(self) -> int:
        default_company = self.get_default_company()
        if default_company:
            return int(default_company["id"])

        return self.create_company(
            slug="default",
            name="Default Company",
            business_type="general",
            active=True,
        )

    # ── WhatsApp channel bindings ─────────────────────────────────────

    @staticmethod
    def _normalize_binding_row(row: Any) -> dict[str, Any]:
        """Give binding rows backend-independent types.

        SQLite returns timestamps as strings while Postgres returns datetimes;
        callers (and the API response model) must not depend on the backend.
        """
        binding = dict(row)
        for timestamp_column in ("created_at", "updated_at"):
            value = binding.get(timestamp_column)
            binding[timestamp_column] = str(value) if value is not None else None
        binding["active"] = int(binding.get("active") or 0)
        if "company_active" in binding:
            binding["company_active"] = int(binding.get("company_active") or 0)
        return binding

    def get_whatsapp_binding_by_phone_number_id(
        self,
        phone_number_id: str,
        *,
        active_only: bool = True,
    ) -> dict[str, Any] | None:
        """Resolve the company bound to a Meta phone_number_id.

        This is the only trusted source of tenant identity for a signed Meta
        webhook, so it joins `companies` and returns the slug the rest of the
        pipeline needs without a second lookup.
        """
        active_condition = "AND b.active = 1" if active_only else ""
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT
                    b.id,
                    b.company_id,
                    b.phone_number_id,
                    b.display_phone_number,
                    b.active,
                    b.created_at,
                    b.updated_at,
                    b.updated_by,
                    c.slug AS company_slug,
                    c.name AS company_name,
                    c.active AS company_active
                FROM whatsapp_channel_bindings b
                JOIN companies c ON c.id = b.company_id
                WHERE b.phone_number_id = ?
                  {active_condition}
                ORDER BY b.id ASC
                """,
                (phone_number_id,),
            )
            rows = cursor.fetchall()

        if len(rows) != 1:
            # Zero means unknown; more than one means an ambiguous binding.
            # Both fail closed at the call site.
            return None
        return self._normalize_binding_row(rows[0])

    def get_whatsapp_bindings(
        self,
        *,
        company_id: int | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        company_id_condition, company_id_params = _optional_equals("b.company_id", company_id)
        active_condition = "AND b.active = 1" if active_only else ""
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT
                    b.id,
                    b.company_id,
                    b.phone_number_id,
                    b.display_phone_number,
                    b.active,
                    b.created_at,
                    b.updated_at,
                    b.updated_by,
                    c.slug AS company_slug
                FROM whatsapp_channel_bindings b
                JOIN companies c ON c.id = b.company_id
                WHERE {company_id_condition}
                  {active_condition}
                ORDER BY b.id ASC
                """,
                (*company_id_params,),
            )
            rows = cursor.fetchall()
        return [self._normalize_binding_row(row) for row in rows]

    def get_active_whatsapp_binding_for_company(self, company_id: int) -> dict[str, Any] | None:
        """Outbound counterpart: which number may this company send from."""
        bindings = self.get_whatsapp_bindings(company_id=company_id, active_only=True)
        if len(bindings) != 1:
            return None
        return bindings[0]

    def upsert_whatsapp_binding(
        self,
        *,
        company_id: int,
        phone_number_id: str,
        display_phone_number: str | None = None,
        active: bool = True,
        updated_by: str | None = None,
    ) -> dict[str, Any] | None:
        """Create or update a binding, keeping phone_number_id unique when active.

        Returns ``None`` when the phone number is already actively bound to a
        different company, so the caller can reject instead of silently
        re-pointing a tenant's traffic.

        The preflight ``SELECT`` only produces a fast, friendly rejection; the
        partial unique index is the actual authority. A concurrent writer that
        wins the race between the SELECT and the write surfaces as the same
        controlled conflict rather than an integrity exception.
        """
        try:
            with db_transaction() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT id, company_id, active
                    FROM whatsapp_channel_bindings
                    WHERE phone_number_id = ?
                    ORDER BY id ASC
                    """,
                    (phone_number_id,),
                )
                existing = [dict(row) for row in cursor.fetchall()]

                conflicting = [
                    row
                    for row in existing
                    if int(row["company_id"]) != int(company_id) and int(row["active"] or 0) == 1
                ]
                if conflicting and active:
                    return None

                owned = [row for row in existing if int(row["company_id"]) == int(company_id)]
                if owned:
                    cursor.execute(
                        """
                        UPDATE whatsapp_channel_bindings
                        SET display_phone_number = ?,
                            active = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            updated_by = ?
                        WHERE id = ?
                        """,
                        (display_phone_number, int(active), updated_by, int(owned[0]["id"])),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO whatsapp_channel_bindings (
                            company_id,
                            phone_number_id,
                            display_phone_number,
                            active,
                            created_at,
                            updated_at,
                            updated_by
                        )
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                        """,
                        (company_id, phone_number_id, display_phone_number, int(active), updated_by),
                    )
        except Exception as exc:
            # Caught outside the transaction block so db_transaction has already
            # rolled back: no partial state can survive a rejected write.
            if _is_active_phone_number_conflict(exc):
                return None
            raise

        # Return the row just written. A historical (deactivated) row for the same
        # number may still exist for another company, so resolve by both keys.
        written = [
            binding
            for binding in self.get_whatsapp_bindings(company_id=company_id)
            if str(binding["phone_number_id"]) == phone_number_id
        ]
        return written[0] if written else None

    def set_whatsapp_binding_active(
        self,
        binding_id: int,
        *,
        active: bool,
        updated_by: str | None = None,
    ) -> str:
        """Activate or deactivate a binding.

        Returns ``"updated"``, ``"not_found"``, or ``"conflict"`` when activating
        a historical binding whose number is already active on another company.
        Deactivation can never conflict.
        """
        try:
            with db_transaction() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    UPDATE whatsapp_channel_bindings
                    SET active = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        updated_by = ?
                    WHERE id = ?
                    """,
                    (int(active), updated_by, binding_id),
                )
                changed = int(cursor.rowcount)
        except Exception as exc:
            if _is_active_phone_number_conflict(exc):
                return "conflict"
            raise

        return "updated" if changed > 0 else "not_found"

    def get_company_context_record(self, company_slug: str) -> dict[str, Any] | None:
        # Context reads sit on the hot message path, so the pooled connection is
        # released even when the query raises.
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    company_slug,
                    context_json,
                    schema_version,
                    revision,
                    created_at,
                    updated_at,
                    updated_by
                FROM company_contexts
                WHERE company_slug = ?
                """,
                (company_slug,),
            )
            row = cursor.fetchone()
        finally:
            connection.close()

        return dict(row) if row else None

    def get_company_context_records(self) -> list[dict[str, Any]]:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    company_slug,
                    schema_version,
                    revision,
                    created_at,
                    updated_at,
                    updated_by
                FROM company_contexts
                ORDER BY company_slug ASC
                """
            )
            rows = cursor.fetchall()
        finally:
            connection.close()

        return [dict(row) for row in rows]

    def save_company_context_record(
        self,
        *,
        company_slug: str,
        company_id: int | None,
        context_json: str,
        schema_version: int,
        expected_revision: int | None = None,
        updated_by: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert or update a company context inside a single transaction.

        Returns ``None`` when the write lost a concurrency check, i.e. another
        writer created or changed the row first. Creation is decided by the
        database, not by a read-then-write window: the INSERT is conditional, so
        exactly one concurrent writer creates revision 1 and the loser gets the
        same controlled conflict as a stale update instead of a unique-violation
        exception. A caller without a revision token can simply retry, which
        then takes the update path.
        """
        connection = get_connection()
        cursor = connection.cursor()

        try:
            cursor.execute(
                "SELECT id, revision FROM company_contexts WHERE company_slug = ?",
                (company_slug,),
            )
            current_row = cursor.fetchone()

            if current_row is None:
                if expected_revision not in (None, 0):
                    return None
                # Conditional insert: the unique index on company_slug decides the
                # winner, so a concurrent creator never raises here.
                if get_database_backend() == "postgres":
                    insert_sql = """
                        INSERT INTO company_contexts (
                            company_id,
                            company_slug,
                            context_json,
                            schema_version,
                            revision,
                            created_at,
                            updated_at,
                            updated_by
                        )
                        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                        ON CONFLICT (company_slug) DO NOTHING
                    """
                else:
                    insert_sql = """
                        INSERT OR IGNORE INTO company_contexts (
                            company_id,
                            company_slug,
                            context_json,
                            schema_version,
                            revision,
                            created_at,
                            updated_at,
                            updated_by
                        )
                        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                    """
                cursor.execute(
                    insert_sql,
                    (
                        company_id,
                        company_slug,
                        context_json,
                        schema_version,
                        updated_by,
                    ),
                )
                if cursor.rowcount != 1:
                    # Another writer created revision 1 between our SELECT and
                    # INSERT. Nothing was written; report the conflict.
                    return None
            else:
                current_revision = int(current_row["revision"])
                if expected_revision is not None and int(expected_revision) != current_revision:
                    return None
                # The revision predicate makes a concurrent update lose instead of
                # silently overwriting the winner.
                cursor.execute(
                    """
                    UPDATE company_contexts
                    SET company_id = ?,
                        context_json = ?,
                        schema_version = ?,
                        revision = revision + 1,
                        updated_at = CURRENT_TIMESTAMP,
                        updated_by = ?
                    WHERE company_slug = ?
                      AND revision = ?
                    """,
                    (
                        company_id,
                        context_json,
                        schema_version,
                        updated_by,
                        company_slug,
                        current_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    return None

            connection.commit()
        finally:
            connection.close()

        return self.get_company_context_record(company_slug)

    def create_calendar_integration(
        self,
        provider: str,
        company_id: int | None = None,
        external_calendar_id: str | None = None,
        config: dict[str, Any] | None = None,
        active: bool = True,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO calendar_integrations (
                company_id,
                provider,
                external_calendar_id,
                config_json,
                active,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                company_id,
                provider,
                external_calendar_id,
                json.dumps(config or {}, ensure_ascii=False),
                int(active),
            ),
        )

        integration_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(integration_id)

    def get_calendar_integrations(
        self,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    provider,
                    external_calendar_id,
                    config_json,
                    active,
                    created_at,
                    updated_at
                FROM calendar_integrations
                ORDER BY id DESC
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    provider,
                    external_calendar_id,
                    config_json,
                    active,
                    created_at,
                    updated_at
                FROM calendar_integrations
                WHERE company_id = ?
                ORDER BY id DESC
                """,
                (company_id,),
            )

        rows = cursor.fetchall()
        connection.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            config_json = item.get("config_json")
            try:
                item["config"] = json.loads(config_json) if config_json else {}
            except json.JSONDecodeError:
                item["config"] = {"raw": config_json}
            item.pop("config_json", None)
            result.append(item)
        return result

    def get_calendar_integration(
        self,
        integration_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                provider,
                external_calendar_id,
                config_json,
                active,
                created_at,
                updated_at
            FROM calendar_integrations
            WHERE id = ?
              AND {company_id_condition}
            """,
            (integration_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()
        if not row:
            return None

        item = dict(row)
        config_json = item.get("config_json")
        try:
            item["config"] = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            item["config"] = {"raw": config_json}
        item.pop("config_json", None)
        return item

    def get_active_calendar_integration(self, company_id: int | None = None) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        if company_id is None:
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    provider,
                    external_calendar_id,
                    config_json,
                    active,
                    created_at,
                    updated_at
                FROM calendar_integrations
                WHERE active = 1
                ORDER BY company_id DESC, id DESC
                LIMIT 1
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    id,
                    company_id,
                    provider,
                    external_calendar_id,
                    config_json,
                    active,
                    created_at,
                    updated_at
                FROM calendar_integrations
                WHERE active = 1
                  AND company_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (company_id,),
            )

        row = cursor.fetchone()
        connection.close()
        if not row:
            return None

        item = dict(row)
        config_json = item.get("config_json")
        try:
            item["config"] = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            item["config"] = {"raw": config_json}
        item.pop("config_json", None)
        return item

    def create_appointment_booking(
        self,
        provider: str,
        title: str,
        starts_at: str,
        ends_at: str,
        timezone: str,
        company_id: int | None = None,
        contact_id: str | None = None,
        integration_id: int | None = None,
        external_event_id: str | None = None,
        scheduling_profile_key: str | None = None,
        customer_profile: str | None = None,
        description: str | None = None,
        status: str = "confirmed",
        resource_id: int | None = None,
    ) -> int:
        """Grava o agendamento; a linha ocupa capacidade no recurso (070).

        Agendamento de empresa sem `resource_id` vai para o recurso `default`
        da empresa, criado na mesma transação se ainda não existir. Sobreposição
        ativa no mesmo recurso levanta `BookingCapacityConflictError`, vinda da
        guarda do banco — não de uma leitura prévia que outra conexão invalida.
        """
        with self._booking_capacity_transaction() as cursor:
            resolved_resource_id = self._resolve_booking_resource_id(
                cursor,
                company_id=company_id,
                resource_id=resource_id,
            )
            # Serializa por (empresa, recurso) antes do INSERT: sem o lock, duas
            # inserções conflitantes podem terminar em deadlock (40P01) em vez
            # da violação da guarda, e o cliente não recebe "horário ocupado".
            self._lock_booking_capacity(cursor, company_id=company_id, resource_id=resolved_resource_id)
            cursor.execute(
                """
                INSERT INTO appointment_bookings (
                    company_id,
                    contact_id,
                    integration_id,
                    resource_id,
                    provider,
                    external_event_id,
                    scheduling_profile_key,
                    customer_profile,
                    title,
                    description,
                    starts_at,
                    ends_at,
                    timezone,
                    status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    company_id,
                    contact_id,
                    integration_id,
                    resolved_resource_id,
                    provider,
                    external_event_id,
                    scheduling_profile_key,
                    customer_profile,
                    title,
                    description,
                    starts_at,
                    ends_at,
                    timezone,
                    status,
                ),
            )
            booking_id = cursor.lastrowid
        return int(booking_id)

    @classmethod
    @contextmanager
    def _booking_capacity_transaction(
        cls,
        *,
        lock_booking_id: int | None = None,
        company_id: int | None = None,
    ) -> Iterator[Any]:
        """Transação de escrita em agendamento sob a guarda de capacidade.

        SQLite toma o lock de escrita antes da primeira leitura (como
        `init_db`). No PostgreSQL, `lock_booking_id` serializa a escrita pelo
        recurso do agendamento (ver `_lock_booking_capacity`). A violação da
        guarda vira `BookingCapacityConflictError`; qualquer outra falha de
        integridade continua propagando como está.
        """
        try:
            with db_transaction() as connection:
                cursor = connection.cursor()
                if get_database_backend() == "sqlite":
                    cursor.execute("BEGIN IMMEDIATE")
                elif lock_booking_id is not None:
                    company_id_condition, company_id_params = _optional_equals("company_id", company_id)
                    cursor.execute(
                        f"""
                        SELECT company_id, resource_id
                        FROM appointment_bookings
                        WHERE id = ?
                          AND {company_id_condition}
                        """,
                        (lock_booking_id, *company_id_params),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        cls._lock_booking_capacity(
                            cursor,
                            company_id=dict(row)["company_id"],
                            resource_id=dict(row)["resource_id"],
                        )
                yield cursor
        except Exception as exc:
            if is_booking_capacity_violation(exc):
                raise BookingCapacityConflictError(
                    "O intervalo já está ocupado por outro agendamento no mesmo recurso."
                ) from exc
            raise

    @staticmethod
    def _lock_booking_capacity(cursor: Any, *, company_id: int | None, resource_id: int | None) -> None:
        """Serializa escritas de capacidade por (empresa, recurso) no PostgreSQL.

        A constraint de exclusão é a garantia; o lock só torna o resultado
        determinístico. Sem ele, inserções conflitantes simultâneas esperam
        uma pela outra dentro da checagem de exclusão e o PostgreSQL aborta
        uma por deadlock (40P01) em vez de devolver a violação da guarda. A
        transação é curta e nunca abrange chamada de rede.
        """
        if company_id is None or resource_id is None or get_database_backend() != "postgres":
            return
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            (f"booking_capacity:{int(company_id)}:{int(resource_id)}",),
        )
        cursor.fetchone()

    @staticmethod
    def _ensure_default_resource_row(cursor: Any, company_id: int) -> dict[str, Any]:
        cursor.execute(
            """
            INSERT INTO booking_resources (company_id, resource_key, label, capacity, active, updated_at)
            VALUES (?, ?, ?, 1, 1, CURRENT_TIMESTAMP)
            ON CONFLICT (company_id, resource_key) DO NOTHING
            """,
            (company_id, DEFAULT_RESOURCE_KEY, DEFAULT_RESOURCE_LABEL),
        )
        cursor.execute(
            """
            SELECT id, company_id, resource_key, label, capacity, active, created_at, updated_at
            FROM booking_resources
            WHERE company_id = ?
              AND resource_key = ?
            """,
            (company_id, DEFAULT_RESOURCE_KEY),
        )
        return dict(cursor.fetchone())

    @classmethod
    def _resolve_booking_resource_id(
        cls,
        cursor: Any,
        *,
        company_id: int | None,
        resource_id: int | None,
    ) -> int | None:
        if company_id is None:
            if resource_id is not None:
                raise BookingResourceUnavailableError("Recurso reservável exige empresa.")
            return None
        if resource_id is None:
            resource = cls._ensure_default_resource_row(cursor, int(company_id))
        else:
            cursor.execute(
                "SELECT id, active FROM booking_resources WHERE id = ? AND company_id = ?",
                (int(resource_id), int(company_id)),
            )
            row = cursor.fetchone()
            if row is None:
                raise BookingResourceUnavailableError("Recurso não encontrado para a empresa.")
            resource = dict(row)
        if not int(resource["active"]):
            raise BookingResourceUnavailableError("Recurso inativo não aceita novos agendamentos.")
        return int(resource["id"])

    def ensure_default_booking_resource(self, company_id: int) -> dict[str, Any]:
        """Recurso `default` (capacidade 1) da empresa, criado se não existir."""
        with self._booking_capacity_transaction() as cursor:
            return self._ensure_default_resource_row(cursor, int(company_id))

    def create_booking_resource(
        self,
        *,
        company_id: int,
        resource_key: str,
        label: str | None = None,
    ) -> dict[str, Any]:
        normalized_key = str(resource_key or "").strip().lower()
        if not RESOURCE_KEY_PATTERN.fullmatch(normalized_key):
            raise ValueError("resource_key must match [a-z0-9][a-z0-9_-]{0,63}.")
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO booking_resources (company_id, resource_key, label, capacity, active, updated_at)
                VALUES (?, ?, ?, 1, 1, CURRENT_TIMESTAMP)
                """,
                (int(company_id), normalized_key, label),
            )
        resource = self.get_booking_resource(company_id=int(company_id), resource_key=normalized_key)
        if resource is None:
            raise RuntimeError("Booking resource not found after creation.")
        return resource

    def get_booking_resource(self, *, company_id: int, resource_key: str) -> dict[str, Any] | None:
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id, company_id, resource_key, label, capacity, active, created_at, updated_at
                FROM booking_resources
                WHERE company_id = ?
                  AND resource_key = ?
                """,
                (int(company_id), str(resource_key)),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_booking_resources(self, *, company_id: int) -> list[dict[str, Any]]:
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id, company_id, resource_key, label, capacity, active, created_at, updated_at
                FROM booking_resources
                WHERE company_id = ?
                ORDER BY resource_key ASC, id ASC
                """,
                (int(company_id),),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def set_booking_resource_active(self, *, company_id: int, resource_key: str, active: bool) -> bool:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE booking_resources
                SET active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE company_id = ?
                  AND resource_key = ?
                """,
                (1 if active else 0, int(company_id), str(resource_key)),
            )
            return int(cursor.rowcount) == 1

    def confirm_appointment_booking_reservation(
        self,
        booking_id: int,
        *,
        external_event_id: str | None,
        company_id: int | None = None,
    ) -> bool:
        """Reserva `pending` → `confirmed` depois do efeito externo (compare-and-set)."""
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with self._booking_capacity_transaction(lock_booking_id=booking_id, company_id=company_id) as cursor:
            cursor.execute(
                f"""
                UPDATE appointment_bookings
                SET status = 'confirmed',
                    external_event_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                  AND {company_id_condition}
                """,
                (external_event_id, booking_id, *company_id_params),
            )
            return int(cursor.rowcount) == 1

    def get_stale_pending_bookings(
        self,
        *,
        created_before: datetime,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Reservas `pending` sem evento externo gravado, criadas antes do limite."""
        if created_before.tzinfo is None:
            raise ValueError("created_before must be timezone-aware")
        # Formato aceito pelos dois bancos: SQLite compara com o texto de
        # CURRENT_TIMESTAMP (UTC, sem "T"); PostgreSQL lê o offset explícito.
        threshold = created_before.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") + "+00:00"
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, company_id, contact_id, provider, integration_id, starts_at, ends_at, timezone, status, created_at
            FROM appointment_bookings
            WHERE status = 'pending'
              AND external_event_id IS NULL
              AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (threshold, max(1, min(int(limit), 500))),
        )
        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def release_appointment_booking_reservation(
        self,
        booking_id: int,
        *,
        company_id: int | None = None,
    ) -> bool:
        """Libera a capacidade de uma reserva cujo efeito externo não aconteceu.

        Só remove a linha ainda `pending` e sem evento externo: nada que já foi
        confirmado ou sincronizado some por este caminho.
        """
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                DELETE FROM appointment_bookings
                WHERE id = ?
                  AND status = 'pending'
                  AND external_event_id IS NULL
                  AND {company_id_condition}
                """,
                (booking_id, *company_id_params),
            )
            return int(cursor.rowcount) == 1

    def hold_appointment_booking_interval(
        self,
        booking_id: int,
        *,
        hold_starts_at: str,
        hold_ends_at: str,
        company_id: int | None = None,
    ) -> bool:
        """Retém o novo intervalo de uma remarcação sem soltar o atual.

        Enquanto o hold existe, a linha ocupa os dois intervalos no recurso; a
        guarda recusa o hold se o novo intervalo estiver ocupado por outra
        linha. Devolve False se o agendamento não está ativo ou já tem hold.
        """
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with self._booking_capacity_transaction(lock_booking_id=booking_id, company_id=company_id) as cursor:
            cursor.execute(
                f"""
                UPDATE appointment_bookings
                SET hold_starts_at = ?,
                    hold_ends_at = ?
                WHERE id = ?
                  AND status IN ('confirmed', 'pending')
                  AND hold_starts_at IS NULL
                  AND {company_id_condition}
                """,
                (hold_starts_at, hold_ends_at, booking_id, *company_id_params),
            )
            return int(cursor.rowcount) == 1

    def clear_appointment_booking_hold(
        self,
        booking_id: int,
        *,
        company_id: int | None = None,
    ) -> None:
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with self._booking_capacity_transaction(lock_booking_id=booking_id, company_id=company_id) as cursor:
            cursor.execute(
                f"""
                UPDATE appointment_bookings
                SET hold_starts_at = NULL,
                    hold_ends_at = NULL
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (booking_id, *company_id_params),
            )

    def get_appointment_bookings(
        self,
        company_id: int | None = None,
        status: str | None = None,
        contact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        contact_id_condition, contact_id_params = _optional_equals("contact_id", contact_id)
        status_condition, status_params = _optional_equals("status", status)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                integration_id,
                provider,
                external_event_id,
                scheduling_profile_key,
                customer_profile,
                title,
                description,
                starts_at,
                ends_at,
                timezone,
                status,
                created_at,
                updated_at,
                resource_id,
                hold_starts_at,
                hold_ends_at
            FROM appointment_bookings
            WHERE {company_id_condition}
              AND {contact_id_condition}
              AND {status_condition}
            ORDER BY starts_at ASC, id ASC
            """,
            (*company_id_params, *contact_id_params, *status_params),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_appointment_booking(
        self,
        booking_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                integration_id,
                provider,
                external_event_id,
                scheduling_profile_key,
                customer_profile,
                title,
                description,
                starts_at,
                ends_at,
                timezone,
                status,
                created_at,
                updated_at,
                resource_id,
                hold_starts_at,
                hold_ends_at
            FROM appointment_bookings
            WHERE id = ?
              AND {company_id_condition}
            """,
            (booking_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_appointment_bookings_between(
        self,
        starts_at: str,
        ends_at: str,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                integration_id,
                provider,
                external_event_id,
                scheduling_profile_key,
                customer_profile,
                title,
                description,
                starts_at,
                ends_at,
                timezone,
                status,
                created_at,
                updated_at,
                resource_id,
                hold_starts_at,
                hold_ends_at
            FROM appointment_bookings
            WHERE {company_id_condition}
              AND status IN ('confirmed', 'pending')
              AND (
                  (starts_at < ? AND ends_at > ?)
                  OR (hold_starts_at IS NOT NULL AND hold_starts_at < ? AND hold_ends_at > ?)
              )
            ORDER BY starts_at ASC, id ASC
            """,
            (*company_id_params, ends_at, starts_at, ends_at, starts_at),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def update_appointment_booking_status(
        self,
        booking_id: int,
        status: str,
        *,
        company_id: int | None = None,
    ) -> None:
        # Reativar um agendamento (cancelled -> confirmed) passa pela mesma
        # guarda de capacidade da criação.
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with self._booking_capacity_transaction() as cursor:
            cursor.execute(
                f"""
                UPDATE appointment_bookings
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (status, booking_id, *company_id_params),
            )

    def update_appointment_booking_schedule(
        self,
        booking_id: int,
        *,
        starts_at: str,
        ends_at: str,
        timezone: str,
        external_event_id: str | None,
        scheduling_profile_key: str | None = None,
        customer_profile: str | None = None,
        title: str | None = None,
        description: str | None = None,
        company_id: int | None = None,
    ) -> bool:
        # O novo horário vira definitivo e o hold da remarcação sai no mesmo
        # UPDATE, sob a guarda de capacidade. Compare-and-set: só aplica em
        # agendamento ainda ativo e com o hold desta remarcação — um
        # cancelamento concorrente durante a chamada ao provider não é
        # sobrescrito com o novo horário.
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        with self._booking_capacity_transaction(lock_booking_id=booking_id, company_id=company_id) as cursor:
            cursor.execute(
                f"""
                UPDATE appointment_bookings
                SET
                    starts_at = ?,
                    ends_at = ?,
                    timezone = ?,
                    external_event_id = ?,
                    scheduling_profile_key = ?,
                    customer_profile = ?,
                    title = COALESCE(?, title),
                    description = COALESCE(?, description),
                    hold_starts_at = NULL,
                    hold_ends_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status IN ('confirmed', 'pending')
                  AND hold_starts_at IS NOT NULL
                  AND {company_id_condition}
                """,
                (starts_at, ends_at, timezone, external_event_id, scheduling_profile_key, customer_profile, title, description, booking_id, *company_id_params),
            )
            return int(cursor.rowcount) == 1

    def get_active_conversation_flow(
        self,
        contact_id: str,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                flow_type,
                status,
                state_json,
                created_at,
                updated_at
            FROM conversation_flows
            WHERE contact_id = ?
              AND {company_id_condition}
              AND status = 'active'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (contact_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()
        if not row:
            return None

        item = dict(row)
        state_json = item.get("state_json")
        try:
            item["state"] = json.loads(state_json) if state_json else {}
        except json.JSONDecodeError:
            item["state"] = {"raw": state_json}
        item.pop("state_json", None)
        return item

    def upsert_conversation_flow(
        self,
        *,
        contact_id: str,
        flow_type: str,
        state: dict[str, Any],
        company_id: int | None = None,
        status: str = "active",
    ) -> int:
        existing = self.get_active_conversation_flow(contact_id=contact_id, company_id=company_id)
        connection = get_connection()
        cursor = connection.cursor()

        if existing:
            cursor.execute(
                """
                UPDATE conversation_flows
                SET
                    flow_type = ?,
                    status = ?,
                    state_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    flow_type,
                    status,
                    json.dumps(state, ensure_ascii=False),
                    existing["id"],
                ),
            )
            flow_id = int(existing["id"])
        else:
            cursor.execute(
                """
                INSERT INTO conversation_flows (
                    company_id,
                    contact_id,
                    flow_type,
                    status,
                    state_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    company_id,
                    contact_id,
                    flow_type,
                    status,
                    json.dumps(state, ensure_ascii=False),
                ),
            )
            flow_id = int(cursor.lastrowid)

        connection.commit()
        connection.close()
        return flow_id

    def close_conversation_flow(
        self,
        flow_id: int,
        *,
        status: str = "completed",
        state: dict[str, Any] | None = None,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        if state is None:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE conversation_flows
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (status, flow_id, *company_id_params),
            )
        else:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE conversation_flows
                SET
                    status = ?,
                    state_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (status, json.dumps(state, ensure_ascii=False), flow_id, *company_id_params),
            )

        connection.commit()
        connection.close()

    def create_customer_reminder(
        self,
        *,
        company_id: int | None,
        contact_id: str,
        reminder_text: str,
        reminder_summary: str | None,
        remind_at: str,
        timezone: str,
        status: str = "pending",
        outbound_message_id: int | None = None,
        audience: str = "customer",
        source: str = "manual",
        appointment_booking_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO customer_reminders (
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
            ),
        )

        reminder_id = int(cursor.lastrowid)
        connection.commit()
        connection.close()
        return reminder_id

    def get_customer_reminders(
        self,
        company_id: int | None = None,
        status: str | None = None,
        audience: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        status_condition, status_params = _optional_equals("status", status)
        audience_condition, audience_params = _optional_equals("audience", audience)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
                attempt_count,
                max_attempts,
                next_attempt_at,
                claimed_at,
                last_error,
                failed_at,
                created_at,
                updated_at
            FROM customer_reminders
            WHERE {company_id_condition}
              AND {status_condition}
              AND {audience_condition}
            ORDER BY remind_at ASC, id ASC
            """,
            (*company_id_params, *status_params, *audience_params),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_customer_reminder(
        self,
        reminder_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
                attempt_count,
                max_attempts,
                next_attempt_at,
                claimed_at,
                last_error,
                failed_at,
                created_at,
                updated_at
            FROM customer_reminders
            WHERE id = ?
              AND {company_id_condition}
            """,
            (reminder_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_due_customer_reminders(
        self,
        reference_time: str,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
                created_at,
                updated_at
            FROM customer_reminders
            WHERE status = 'pending'
              AND remind_at <= ?
              AND {company_id_condition}
            ORDER BY remind_at ASC, id ASC
            """,
            (reference_time, *company_id_params),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def claim_due_customer_reminder(
        self,
        reference_time: str,
        *,
        claim_token: str,
        audience: str | None = None,
        company_id: int | None = None,
        claim_ttl_seconds: int = 300,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        stale_before_at = (
            now - timedelta(seconds=max(30, claim_ttl_seconds))
        )
        stale_before = _iso_timestamp(stale_before_at)
        company_condition, company_params = _optional_equals("company_id", company_id)
        audience_condition, audience_params = _optional_equals("audience", audience)
        lock_clause = " FOR UPDATE SKIP LOCKED" if get_database_backend() == "postgres" else ""
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT id
                FROM customer_reminders
                WHERE (
                    status = 'pending'
                    OR (
                        status = 'processing'
                        AND claimed_at <= ?
                    )
                )
                  AND remind_at <= ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND {company_condition}
                  AND {audience_condition}
                ORDER BY remind_at ASC, id ASC
                LIMIT 1{lock_clause}
                """,
                (
                    stale_before,
                    reference_time,
                    reference_time,
                    *company_params,
                    *audience_params,
                ),
            )
            candidate = cursor.fetchone()
            if not candidate:
                return None
            reminder_id = int(candidate["id"])
            cursor.execute(
                """
                UPDATE customer_reminders
                SET status = 'processing',
                    attempt_count = attempt_count + 1,
                    claimed_at = ?,
                    claim_token = ?,
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (
                      status = 'pending'
                      OR (
                          status = 'processing'
                          AND claimed_at <= ?
                      )
                  )
                """,
                (_iso_timestamp(now), claim_token, reminder_id, stale_before),
            )
            if cursor.rowcount != 1:
                return None
            cursor.execute(
                """
                SELECT *
                FROM customer_reminders
                WHERE id = ?
                  AND claim_token = ?
                """,
                (reminder_id, claim_token),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def enqueue_claimed_reminder_outbound(
        self,
        reminder_id: int,
        *,
        claim_token: str,
        channel: str,
        message: str,
        source: str,
        template_parameters: list[str] | None = None,
    ) -> int:
        """Atomically create one outbound and close one reminder claim."""
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT contact_id, company_id
                FROM customer_reminders
                WHERE id = ?
                  AND claim_token = ?
                  AND status = 'processing'
                """,
                (reminder_id, claim_token),
            )
            reminder = cursor.fetchone()
            if not reminder:
                raise RuntimeError("Reminder claim was lost before outbound enqueue.")
            cursor.execute(
                """
                INSERT INTO outbound_messages (
                    contact_id,
                    company_id,
                    message,
                    source,
                    status,
                    channel,
                    template_parameters_json
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    reminder["contact_id"],
                    reminder["company_id"],
                    message,
                    source,
                    channel,
                    json.dumps(template_parameters, ensure_ascii=False) if template_parameters is not None else None,
                ),
            )
            outbound_message_id = int(cursor.lastrowid)
            cursor.execute(
                """
                UPDATE customer_reminders
                SET status = 'sent',
                    outbound_message_id = ?,
                    claimed_at = NULL,
                    claim_token = NULL,
                    next_attempt_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND claim_token = ?
                  AND status = 'processing'
                """,
                (outbound_message_id, reminder_id, claim_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Reminder claim was lost during outbound enqueue.")
            return outbound_message_id

    def mark_customer_reminder_failed(
        self,
        reminder_id: int,
        *,
        claim_token: str,
        error: str,
        retry_at: str | None,
    ) -> str:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT attempt_count, max_attempts
                FROM customer_reminders
                WHERE id = ?
                  AND claim_token = ?
                  AND status = 'processing'
                """,
                (reminder_id, claim_token),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("Reminder claim was lost before failure handling.")
            can_retry = (
                retry_at is not None
                and int(row["attempt_count"]) < int(row["max_attempts"])
            )
            status = "pending" if can_retry else "failed"
            cursor.execute(
                """
                UPDATE customer_reminders
                SET status = ?,
                    next_attempt_at = ?,
                    last_error = ?,
                    failed_at = CASE
                        WHEN ? = 'failed' THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    claimed_at = NULL,
                    claim_token = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND claim_token = ?
                  AND status = 'processing'
                """,
                (
                    status,
                    retry_at if can_retry else None,
                    str(error)[:2000],
                    status,
                    reminder_id,
                    claim_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Reminder claim was lost during failure handling.")
            return status

    def get_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        audience: str = "customer",
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                reminder_text,
                reminder_summary,
                remind_at,
                timezone,
                status,
                outbound_message_id,
                audience,
                source,
                appointment_booking_id,
                created_at,
                updated_at
            FROM customer_reminders
            WHERE appointment_booking_id = ?
              AND audience = ?
              AND {company_id_condition}
            ORDER BY remind_at ASC, id ASC
            """,
            (appointment_booking_id, audience, *company_id_params),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def mark_customer_reminder_sent(
        self,
        reminder_id: int,
        *,
        outbound_message_id: int | None = None,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE customer_reminders
            SET
                status = 'sent',
                outbound_message_id = ?,
                claimed_at = NULL,
                claim_token = NULL,
                next_attempt_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            (outbound_message_id, reminder_id, *company_id_params),
        )

        connection.commit()
        connection.close()

    def cancel_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        *,
        company_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE customer_reminders
            SET
                status = 'cancelled',
                updated_at = CURRENT_TIMESTAMP
            WHERE appointment_booking_id = ?
              AND status = 'pending'
              AND {company_id_condition}
            """,
            (appointment_booking_id, *company_id_params),
        )

        updated_count = int(cursor.rowcount)
        connection.commit()
        connection.close()
        return updated_count

    def create_appointment_queue_entry(
        self,
        *,
        company_id: int | None,
        contact_id: str,
        desired_title: str,
        scheduling_profile_key: str | None,
        customer_profile: str | None,
        request_summary: str | None,
        timezone: str,
        earliest_starts_at: str | None,
        latest_starts_at: str | None,
        preferred_periods: list[str] | None,
        priority_tier: str,
        priority_score: int,
        accepts_short_notice: bool,
        original_booking_id: int | None,
        status: str = "active",
        source: str = "manual",
        notes: str | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO appointment_queue_entries (
                company_id,
                contact_id,
                desired_title,
                scheduling_profile_key,
                customer_profile,
                request_summary,
                timezone,
                earliest_starts_at,
                latest_starts_at,
                preferred_periods_json,
                priority_tier,
                priority_score,
                accepts_short_notice,
                original_booking_id,
                status,
                source,
                notes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                company_id,
                contact_id,
                desired_title,
                scheduling_profile_key,
                customer_profile,
                request_summary,
                timezone,
                earliest_starts_at,
                latest_starts_at,
                json.dumps(preferred_periods or [], ensure_ascii=False),
                priority_tier,
                priority_score,
                int(accepts_short_notice),
                original_booking_id,
                status,
                source,
                notes,
            ),
        )

        entry_id = int(cursor.lastrowid)
        connection.commit()
        connection.close()
        return entry_id

    def get_appointment_queue_entries(
        self,
        *,
        company_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        status_condition, status_params = _optional_equals("status", status)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                contact_id,
                desired_title,
                scheduling_profile_key,
                customer_profile,
                request_summary,
                timezone,
                earliest_starts_at,
                latest_starts_at,
                preferred_periods_json,
                priority_tier,
                priority_score,
                accepts_short_notice,
                original_booking_id,
                status,
                source,
                notes,
                created_at,
                updated_at
            FROM appointment_queue_entries
            WHERE {company_id_condition}
              AND {status_condition}
            ORDER BY priority_score DESC, created_at ASC, id ASC
            """,
            (*company_id_params, *status_params),
        )

        rows = cursor.fetchall()
        connection.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            preferred_periods_json = item.get("preferred_periods_json")
            try:
                item["preferred_periods"] = json.loads(preferred_periods_json) if preferred_periods_json else []
            except json.JSONDecodeError:
                item["preferred_periods"] = []
            item.pop("preferred_periods_json", None)
            result.append(item)
        return result

    def get_appointment_queue_entry(
        self,
        entry_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        entries = self.get_appointment_queue_entries(company_id=company_id)
        for entry in entries:
            if int(entry["id"]) == entry_id:
                return entry
        return None

    def update_appointment_queue_entry_status(
        self,
        entry_id: int,
        *,
        status: str,
        notes: str | None = None,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        if notes is None:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE appointment_queue_entries
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (status, entry_id, *company_id_params),
            )
        else:
            company_id_condition, company_id_params = _optional_equals("company_id", company_id)
            cursor.execute(
                f"""
                UPDATE appointment_queue_entries
                SET
                    status = ?,
                    notes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND {company_id_condition}
                """,
                (status, notes, entry_id, *company_id_params),
            )

        connection.commit()
        connection.close()

    def get_cancelled_future_bookings_without_offers(
        self,
        reference_time: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Vagas canceladas futuras, sem oferta, em empresa com fila ativa.

        Alimenta a recuperação de ofertas adiadas quando o encaixe falhou logo
        após o cancelamento.
        """
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT booking.id, booking.company_id
            FROM appointment_bookings AS booking
            WHERE booking.status = 'cancelled'
              AND booking.company_id IS NOT NULL
              AND booking.starts_at > ?
              AND NOT EXISTS (
                  SELECT 1 FROM appointment_queue_offers AS offer
                  WHERE offer.company_id = booking.company_id
                    AND offer.released_booking_id = booking.id
              )
              AND EXISTS (
                  SELECT 1 FROM appointment_queue_entries AS entry
                  WHERE entry.company_id = booking.company_id
                    AND entry.status = 'active'
              )
            ORDER BY booking.starts_at ASC, booking.id ASC
            LIMIT ?
            """,
            (reference_time, max(1, min(int(limit), 500))),
        )
        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def create_appointment_queue_offer(
        self,
        *,
        company_id: int | None,
        queue_entry_id: int,
        released_booking_id: int | None,
        outbound_message_id: int | None,
        new_booking_id: int | None,
        offered_starts_at: str,
        offered_ends_at: str,
        timezone: str,
        status: str = "pending",
        response_deadline_at: str | None = None,
        follow_up_after: str | None = None,
        responded_at: str | None = None,
        response_notes: str | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO appointment_queue_offers (
                company_id,
                queue_entry_id,
                released_booking_id,
                outbound_message_id,
                new_booking_id,
                offered_starts_at,
                offered_ends_at,
                timezone,
                status,
                response_deadline_at,
                follow_up_after,
                responded_at,
                response_notes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                company_id,
                queue_entry_id,
                released_booking_id,
                outbound_message_id,
                new_booking_id,
                offered_starts_at,
                offered_ends_at,
                timezone,
                status,
                response_deadline_at,
                follow_up_after,
                responded_at,
                response_notes,
            ),
        )

        offer_id = int(cursor.lastrowid)
        connection.commit()
        connection.close()
        return offer_id

    def get_appointment_queue_offers(
        self,
        *,
        company_id: int | None = None,
        status: str | None = None,
        released_booking_id: int | None = None,
        queue_entry_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        status_condition, status_params = _optional_equals("status", status)
        released_booking_id_condition, released_booking_id_params = _optional_equals("released_booking_id", released_booking_id)
        queue_entry_id_condition, queue_entry_id_params = _optional_equals("queue_entry_id", queue_entry_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                queue_entry_id,
                released_booking_id,
                outbound_message_id,
                new_booking_id,
                offered_starts_at,
                offered_ends_at,
                timezone,
                status,
                response_deadline_at,
                follow_up_after,
                responded_at,
                response_notes,
                created_at,
                updated_at
            FROM appointment_queue_offers
            WHERE {company_id_condition}
              AND {status_condition}
              AND {released_booking_id_condition}
              AND {queue_entry_id_condition}
            ORDER BY created_at ASC, id ASC
            """,
            (*company_id_params, *status_params, *released_booking_id_params, *queue_entry_id_params),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_appointment_queue_offer(
        self,
        offer_id: int,
        *,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            SELECT
                id,
                company_id,
                queue_entry_id,
                released_booking_id,
                outbound_message_id,
                new_booking_id,
                offered_starts_at,
                offered_ends_at,
                timezone,
                status,
                response_deadline_at,
                follow_up_after,
                responded_at,
                response_notes,
                created_at,
                updated_at
            FROM appointment_queue_offers
            WHERE id = ?
              AND {company_id_condition}
            """,
            (offer_id, *company_id_params),
        )

        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def update_appointment_queue_offer(
        self,
        offer_id: int,
        *,
        status: str,
        outbound_message_id: int | None = None,
        new_booking_id: int | None = None,
        response_deadline_at: str | None = None,
        follow_up_after: str | None = None,
        responded_at: str | None = None,
        response_notes: str | None = None,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE appointment_queue_offers
            SET
                status = ?,
                outbound_message_id = COALESCE(?, outbound_message_id),
                new_booking_id = COALESCE(?, new_booking_id),
                response_deadline_at = ?,
                follow_up_after = ?,
                responded_at = ?,
                response_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            (status, outbound_message_id, new_booking_id, response_deadline_at, follow_up_after, responded_at, response_notes, offer_id, *company_id_params),
        )

        connection.commit()
        connection.close()

    # ── Analytics ──────────────────────────────────────────────────────

    def update_appointment_booking_outcome(
        self,
        booking_id: int,
        *,
        outcome: str,
        notes: str | None = None,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE appointment_bookings
            SET booking_outcome = ?,
                booking_outcome_at = CURRENT_TIMESTAMP,
                booking_outcome_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            (outcome, notes, booking_id, *company_id_params),
        )
        connection.commit()
        connection.close()

    def update_appointment_booking_cancellation_reason(
        self,
        booking_id: int,
        reason: str,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        cursor.execute(
            f"""
            UPDATE appointment_bookings
            SET cancellation_reason = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            (reason, booking_id, *company_id_params),
        )
        connection.commit()
        connection.close()

    def get_analytics_bookings_summary(
        self,
        *,
        company_id: int | None = None,
        starts_after: str | None = None,
        starts_before: str | None = None,
    ) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        conditions: list[str] = []
        params: list[Any] = []
        if company_id is not None:
            conditions.append("company_id = ?")
            params.append(company_id)
        if starts_after:
            conditions.append("starts_at >= ?")
            params.append(starts_after)
        if starts_before:
            conditions.append("starts_at < ?")
            params.append(starts_before)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        cursor.execute(
            f"SELECT COUNT(*) AS count FROM appointment_bookings{where_clause}",
            tuple(params),
        )
        total = cursor.fetchone()["count"]

        cursor.execute(
            f"""
            SELECT status AS group_key, COUNT(*) AS count
            FROM appointment_bookings{where_clause}
            GROUP BY status
            """,
            tuple(params),
        )
        by_status = {str(row["group_key"] or "unknown"): int(row["count"]) for row in cursor.fetchall()}

        cursor.execute(
            f"""
            SELECT booking_outcome AS group_key, COUNT(*) AS count
            FROM appointment_bookings{where_clause}
            GROUP BY booking_outcome
            """,
            tuple(params),
        )
        by_outcome = {str(row["group_key"] or "pending"): int(row["count"]) for row in cursor.fetchall()}

        cancel_conditions = [*list(conditions), "status = 'cancelled'", "cancellation_reason IS NOT NULL", "cancellation_reason != ''"]
        cursor.execute(
            f"""
            SELECT cancellation_reason AS group_key, COUNT(*) AS count
            FROM appointment_bookings
            WHERE {' AND '.join(cancel_conditions)}
            GROUP BY cancellation_reason
            ORDER BY count DESC
            """,
            tuple(params),
        )
        cancellation_reasons = {str(row["group_key"]): int(row["count"]) for row in cursor.fetchall()}

        connection.close()
        return {
            "total": total,
            "by_status": by_status,
            "by_outcome": by_outcome,
            "cancellation_reasons": cancellation_reasons,
        }

    def get_analytics_reminders_summary(
        self,
        *,
        company_id: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        conditions: list[str] = ["audience = 'customer'"]
        params: list[Any] = []
        if company_id is not None:
            conditions.append("company_id = ?")
            params.append(company_id)
        if created_after:
            conditions.append("created_at >= ?")
            params.append(created_after)
        if created_before:
            conditions.append("created_at < ?")
            params.append(created_before)

        where_clause = f" WHERE {' AND '.join(conditions)}"

        cursor.execute(
            f"SELECT COUNT(*) AS count FROM customer_reminders{where_clause}",
            tuple(params),
        )
        total = cursor.fetchone()["count"]

        cursor.execute(
            f"""
            SELECT status AS group_key, COUNT(*) AS count
            FROM customer_reminders{where_clause}
            GROUP BY status
            """,
            tuple(params),
        )
        by_status = {str(row["group_key"] or "unknown"): int(row["count"]) for row in cursor.fetchall()}

        connection.close()
        return {
            "total": total,
            "by_status": by_status,
            "sent": by_status.get("sent", 0),
            "pending": by_status.get("pending", 0),
            "cancelled": by_status.get("cancelled", 0),
        }

    def get_analytics_queue_summary(
        self,
        *,
        company_id: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        entry_conditions: list[str] = []
        entry_params: list[Any] = []
        if company_id is not None:
            entry_conditions.append("company_id = ?")
            entry_params.append(company_id)
        if created_after:
            entry_conditions.append("created_at >= ?")
            entry_params.append(created_after)
        if created_before:
            entry_conditions.append("created_at < ?")
            entry_params.append(created_before)

        entry_where = f" WHERE {' AND '.join(entry_conditions)}" if entry_conditions else ""

        cursor.execute(
            f"SELECT COUNT(*) AS count FROM appointment_queue_entries{entry_where}",
            tuple(entry_params),
        )
        total_entries = cursor.fetchone()["count"]

        cursor.execute(
            f"""
            SELECT status AS group_key, COUNT(*) AS count
            FROM appointment_queue_entries{entry_where}
            GROUP BY status
            """,
            tuple(entry_params),
        )
        entries_by_status = {str(row["group_key"] or "unknown"): int(row["count"]) for row in cursor.fetchall()}

        offer_conditions: list[str] = []
        offer_params: list[Any] = []
        if company_id is not None:
            offer_conditions.append("company_id = ?")
            offer_params.append(company_id)
        if created_after:
            offer_conditions.append("created_at >= ?")
            offer_params.append(created_after)
        if created_before:
            offer_conditions.append("created_at < ?")
            offer_params.append(created_before)

        offer_where = f" WHERE {' AND '.join(offer_conditions)}" if offer_conditions else ""

        cursor.execute(
            f"SELECT COUNT(*) AS count FROM appointment_queue_offers{offer_where}",
            tuple(offer_params),
        )
        total_offers = cursor.fetchone()["count"]

        cursor.execute(
            f"""
            SELECT status AS group_key, COUNT(*) AS count
            FROM appointment_queue_offers{offer_where}
            GROUP BY status
            """,
            tuple(offer_params),
        )
        offers_by_status = {str(row["group_key"] or "unknown"): int(row["count"]) for row in cursor.fetchall()}

        connection.close()
        return {
            "entries": {
                "total": total_entries,
                "by_status": entries_by_status,
            },
            "offers": {
                "total": total_offers,
                "by_status": offers_by_status,
                "accepted": offers_by_status.get("accepted", 0),
                "expired": offers_by_status.get("expired", 0),
                "declined": offers_by_status.get("declined", 0),
            },
            "recovery_rate": round(
                offers_by_status.get("accepted", 0) / max(total_offers, 1) * 100, 1
            ),
        }

    def get_analytics_monthly_trend(
        self,
        *,
        company_id: int | None = None,
        months: int = 6,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        conditions: list[str] = []
        params: list[Any] = []
        if company_id is not None:
            conditions.append("company_id = ?")
            params.append(company_id)

        where_clause = f" AND {' AND '.join(conditions)}" if conditions else ""

        backend = get_database_backend()
        month_extract = "TO_CHAR(starts_at, 'YYYY-MM')" if backend == "postgres" else "strftime('%Y-%m', starts_at)"

        cursor.execute(
            f"""
            SELECT
                {month_extract} AS month,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN booking_outcome = 'attended' THEN 1 ELSE 0 END) AS attended,
                SUM(CASE WHEN booking_outcome = 'no_show' THEN 1 ELSE 0 END) AS no_show
            FROM appointment_bookings
            WHERE starts_at >= datetime('now', ?)
            {where_clause}
            GROUP BY {month_extract}
            ORDER BY month ASC
            """,
            tuple([f"-{months} months", *params]),
        )

        trend = []
        for row in cursor.fetchall():
            total = int(row["total"] or 0)
            attended = int(row["attended"] or 0)
            no_show = int(row["no_show"] or 0)
            resolved = attended + no_show
            trend.append({
                "month": str(row["month"]),
                "total": total,
                "confirmed": int(row["confirmed"] or 0),
                "cancelled": int(row["cancelled"] or 0),
                "attended": attended,
                "no_show": no_show,
                "no_show_rate": round(no_show / max(resolved, 1) * 100, 1),
                "attendance_rate": round(attended / max(resolved, 1) * 100, 1),
            })

        connection.close()
        return trend

    def get_analytics_reminder_effectiveness(
        self,
        *,
        company_id: int | None = None,
        starts_after: str | None = None,
        starts_before: str | None = None,
    ) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        conditions: list[str] = []
        params: list[Any] = []
        if company_id is not None:
            conditions.append("b.company_id = ?")
            params.append(company_id)
        if starts_after:
            conditions.append("b.starts_at >= ?")
            params.append(starts_after)
        if starts_before:
            conditions.append("b.starts_at < ?")
            params.append(starts_before)

        where_clause = f" AND {' AND '.join(conditions)}" if conditions else ""

        cursor.execute(
            f"""
            SELECT
                COUNT(DISTINCT b.id) AS total_bookings,
                COUNT(DISTINCT CASE WHEN r.id IS NOT NULL AND r.status = 'sent' THEN b.id END) AS with_reminder,
                COUNT(DISTINCT CASE WHEN r.id IS NULL OR r.status != 'sent' THEN b.id END) AS without_reminder
            FROM appointment_bookings b
            LEFT JOIN customer_reminders r
                ON r.appointment_booking_id = b.id AND r.audience = 'customer' AND r.status = 'sent'
            WHERE b.booking_outcome IS NOT NULL
            {where_clause}
            """,
            tuple(params),
        )
        totals = cursor.fetchone()

        cursor.execute(
            f"""
            SELECT
                CASE WHEN r.id IS NOT NULL AND r.status = 'sent' THEN 'with_reminder' ELSE 'without_reminder' END AS reminder_group,
                b.booking_outcome,
                COUNT(*) AS count
            FROM appointment_bookings b
            LEFT JOIN customer_reminders r
                ON r.appointment_booking_id = b.id AND r.audience = 'customer' AND r.status = 'sent'
            WHERE b.booking_outcome IS NOT NULL
            {where_clause}
            GROUP BY reminder_group, b.booking_outcome
            """,
            tuple(params),
        )

        breakdown: dict[str, dict[str, int]] = {
            "with_reminder": {},
            "without_reminder": {},
        }
        for row in cursor.fetchall():
            group = str(row["reminder_group"])
            outcome = str(row["booking_outcome"])
            breakdown.setdefault(group, {})[outcome] = int(row["count"])

        with_r = breakdown.get("with_reminder", {})
        without_r = breakdown.get("without_reminder", {})

        with_total = sum(with_r.values()) or 1
        without_total = sum(without_r.values()) or 1

        connection.close()
        return {
            "total_bookings_with_outcome": int(totals["total_bookings"] or 0),
            "with_reminder": {
                "total": int(totals["with_reminder"] or 0),
                "attended": with_r.get("attended", 0),
                "no_show": with_r.get("no_show", 0),
                "no_show_rate": round(with_r.get("no_show", 0) / with_total * 100, 1),
                "attendance_rate": round(with_r.get("attended", 0) / with_total * 100, 1),
            },
            "without_reminder": {
                "total": int(totals["without_reminder"] or 0),
                "attended": without_r.get("attended", 0),
                "no_show": without_r.get("no_show", 0),
                "no_show_rate": round(without_r.get("no_show", 0) / without_total * 100, 1),
                "attendance_rate": round(without_r.get("attended", 0) / without_total * 100, 1),
            },
            "reminder_impact": {
                "no_show_reduction_pp": round(
                    without_r.get("no_show", 0) / without_total * 100
                    - with_r.get("no_show", 0) / with_total * 100,
                    1,
                ),
            },
        }

    # ── Billing ────────────────────────────────────────────────────────

    def upsert_billing_plan(
        self,
        plan_key: str,
        name: str,
        price_monthly_cents: int,
        limits_json: str,
        features_json: str,
        display_order: int = 0,
        price_annual_monthly_cents: int | None = None,
        currency: str = "brl",
        stripe_product_id: str | None = None,
        stripe_monthly_price_id: str | None = None,
        stripe_annual_price_id: str | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("SELECT id FROM billing_plans WHERE plan_key = ?", (plan_key,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE billing_plans
                SET name = ?, price_monthly_cents = ?, price_annual_monthly_cents = ?,
                    limits_json = ?, features_json = ?, display_order = ?, active = 1,
                    currency = ?, stripe_product_id = ?,
                    stripe_monthly_price_id = ?, stripe_annual_price_id = ?
                WHERE plan_key = ?
                """,
                (name, price_monthly_cents, price_annual_monthly_cents,
                 limits_json, features_json, display_order, currency,
                 stripe_product_id, stripe_monthly_price_id,
                 stripe_annual_price_id, plan_key),
            )
            plan_id = int(existing["id"])
        else:
            cursor.execute(
                """
                INSERT INTO billing_plans (plan_key, name, price_monthly_cents,
                    price_annual_monthly_cents, limits_json, features_json, display_order,
                    currency, stripe_product_id, stripe_monthly_price_id,
                    stripe_annual_price_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_key, name, price_monthly_cents, price_annual_monthly_cents,
                 limits_json, features_json, display_order, currency,
                 stripe_product_id, stripe_monthly_price_id,
                 stripe_annual_price_id),
            )
            plan_id = cursor.lastrowid

        connection.commit()
        connection.close()
        return int(plan_id)

    def get_billing_plans(self, active_only: bool = True) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()
        query = "SELECT * FROM billing_plans"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY display_order ASC"
        cursor.execute(query)
        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def update_billing_plan_gateway_mapping(
        self,
        plan_id: int,
        *,
        currency: str,
        stripe_product_id: str | None = None,
        stripe_monthly_price_id: str | None = None,
        stripe_annual_price_id: str | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE billing_plans
            SET currency = ?,
                stripe_product_id = ?,
                stripe_monthly_price_id = ?,
                stripe_annual_price_id = ?
            WHERE id = ?
            """,
            (
                currency,
                stripe_product_id,
                stripe_monthly_price_id,
                stripe_annual_price_id,
                plan_id,
            ),
        )
        connection.commit()
        connection.close()

    def get_billing_plan(self, plan_key: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM billing_plans WHERE plan_key = ?", (plan_key,))
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_billing_plan_by_price_id(self, stripe_price_id: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM billing_plans
            WHERE stripe_monthly_price_id = ?
               OR stripe_annual_price_id = ?
            LIMIT 1
            """,
            (stripe_price_id, stripe_price_id),
        )
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def create_billing_subscription(
        self,
        company_id: int,
        plan_key: str,
        status: str,
        billing_cycle: str,
        current_period_start: str,
        current_period_end: str,
        trial_ends_at: str | None = None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        stripe_price_id: str | None = None,
        gateway: str | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO billing_subscriptions (
                company_id, plan_key, status, billing_cycle,
                current_period_start, current_period_end, trial_ends_at,
                stripe_customer_id, stripe_subscription_id, stripe_price_id, gateway
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, plan_key, status, billing_cycle,
             current_period_start, current_period_end, trial_ends_at,
             stripe_customer_id, stripe_subscription_id, stripe_price_id, gateway),
        )
        sub_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(sub_id)

    def get_billing_subscription(self, company_id: int) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT * FROM billing_subscriptions
            WHERE company_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (company_id,),
        )
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_billing_subscription_by_stripe_id(
        self,
        stripe_subscription_id: str,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM billing_subscriptions
            WHERE stripe_subscription_id = ?
            LIMIT 1
            """,
            (stripe_subscription_id,),
        )
        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def update_billing_subscription(
        self,
        subscription_id: int,
        *,
        company_id: int | None = None,
        **fields: Any,
    ) -> None:
        allowed = {
            "plan_key", "status", "billing_cycle", "current_period_start",
            "current_period_end", "trial_ends_at", "stripe_customer_id",
            "stripe_subscription_id", "cancelled_at", "cancel_reason",
            "gateway", "stripe_price_id", "payment_status",
            "cancel_at_period_end", "grace_period_ends_at", "last_invoice_id",
            "gateway_event_created", "gateway_event_id", "gateway_updated_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return

        # Column names come from the `allowed` whitelist above, never from callers.
        # Keys come from the `allowed` whitelist above; validating them here
        # makes the SET clause provably safe at the point of construction.
        set_clause = ", ".join(
            f"{_assert_sql_identifier(column)} = ?" for column in updates
        )
        company_id_condition, company_id_params = _optional_equals("company_id", company_id)
        params = [*list(updates.values()), subscription_id, *company_id_params]

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE billing_subscriptions
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND {company_id_condition}
            """,
            tuple(params),
        )
        connection.commit()
        connection.close()

    def claim_billing_webhook_event(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str,
        event_created: int,
        livemode: bool,
        payload_hash: str,
        processing_timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        stale_before_at = (
            datetime.now(UTC)
            - timedelta(seconds=max(30, processing_timeout_seconds))
        )
        stale_before = _iso_timestamp(stale_before_at)
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO billing_webhook_events (
                    provider,
                    event_id,
                    event_type,
                    event_created,
                    livemode,
                    payload_hash,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'processing')
                ON CONFLICT (provider, event_id) DO NOTHING
                """,
                (
                    provider,
                    event_id,
                    event_type,
                    event_created,
                    1 if livemode else 0,
                    payload_hash,
                ),
            )
            if cursor.rowcount == 1:
                return {"claimed": True, "status": "processing", "duplicate": False}
            cursor.execute(
                """
                SELECT status, attempt_count, payload_hash, updated_at
                FROM billing_webhook_events
                WHERE provider = ?
                  AND event_id = ?
                """,
                (provider, event_id),
            )
            existing = cursor.fetchone()
            if not existing:
                raise RuntimeError("Webhook event conflict could not be re-read.")
            if not hmac.compare_digest(str(existing["payload_hash"]), payload_hash):
                raise RuntimeError("Webhook event id was replayed with a different payload.")
            existing_status = str(existing["status"])
            retryable_status = existing_status == "failed"
            if existing_status == "processing":
                raw_updated_at = existing["updated_at"]
                if isinstance(raw_updated_at, datetime):
                    updated_at = raw_updated_at
                else:
                    updated_at = datetime.fromisoformat(
                        str(raw_updated_at).replace("Z", "+00:00")
                    )
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                retryable_status = updated_at.astimezone(UTC) <= datetime.fromisoformat(
                    stale_before
                )
            if retryable_status:
                cursor.execute(
                    """
                    UPDATE billing_webhook_events
                    SET status = 'processing',
                        attempt_count = attempt_count + 1,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE provider = ?
                      AND event_id = ?
                      AND (
                          status = 'failed'
                          OR (
                              status = 'processing'
                              AND updated_at <= ?
                          )
                      )
                    """,
                    (provider, event_id, stale_before),
                )
                if cursor.rowcount == 1:
                    return {
                        "claimed": True,
                        "status": "processing",
                        "duplicate": True,
                    }
            return {
                "claimed": False,
                "status": str(existing["status"]),
                "duplicate": True,
            }

    def complete_billing_webhook_event(
        self,
        *,
        provider: str,
        event_id: str,
        status: str,
        company_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"processed", "ignored", "failed"}:
            raise ValueError("Invalid billing webhook completion status.")
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE billing_webhook_events
                SET status = ?,
                    company_id = COALESCE(?, company_id),
                    last_error = ?,
                    processed_at = CASE
                        WHEN ? IN ('processed', 'ignored') THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = ?
                  AND event_id = ?
                """,
                (
                    status,
                    company_id,
                    str(error)[:2000] if error else None,
                    status,
                    provider,
                    event_id,
                ),
            )

    def reconcile_billing_subscription(
        self,
        *,
        company_id: int,
        plan_key: str,
        status: str,
        billing_cycle: str,
        current_period_start: str,
        current_period_end: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        stripe_price_id: str,
        event_created: int,
        event_id: str,
        trial_ends_at: str | None = None,
        payment_status: str | None = None,
        cancel_at_period_end: bool = False,
        grace_period_ends_at: str | None = None,
        last_invoice_id: str | None = None,
    ) -> dict[str, Any]:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT *
                FROM billing_subscriptions
                WHERE stripe_subscription_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (stripe_subscription_id,),
            )
            existing = cursor.fetchone()
            if not existing:
                cursor.execute(
                    """
                    SELECT *
                    FROM billing_subscriptions
                    WHERE company_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (company_id,),
                )
                existing = cursor.fetchone()

            if existing:
                previous_event_created = int(existing["gateway_event_created"] or 0)
                terminal_regression_at_same_second = (
                    previous_event_created == event_created
                    and str(existing["status"]) == "cancelled"
                    and status != "cancelled"
                )
                if previous_event_created > event_created or terminal_regression_at_same_second:
                    return {
                        "applied": False,
                        "reason": "stale_event",
                        "subscription_id": int(existing["id"]),
                    }
                cursor.execute(
                    """
                    UPDATE billing_subscriptions
                    SET company_id = ?,
                        plan_key = ?,
                        status = ?,
                        billing_cycle = ?,
                        trial_ends_at = ?,
                        current_period_start = ?,
                        current_period_end = ?,
                        stripe_customer_id = ?,
                        stripe_subscription_id = ?,
                        stripe_price_id = ?,
                        gateway = 'stripe',
                        payment_status = COALESCE(?, payment_status),
                        cancel_at_period_end = ?,
                        grace_period_ends_at = CASE
                            WHEN ? = 'paid' THEN NULL
                            WHEN CAST(? AS TEXT) IS NULL THEN grace_period_ends_at
                            ELSE ?
                        END,
                        last_invoice_id = COALESCE(?, last_invoice_id),
                        gateway_event_created = ?,
                        gateway_event_id = ?,
                        gateway_updated_at = CURRENT_TIMESTAMP,
                        cancelled_at = CASE
                            WHEN ? = 'cancelled' THEN COALESCE(cancelled_at, CURRENT_TIMESTAMP)
                            ELSE NULL
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        company_id,
                        plan_key,
                        status,
                        billing_cycle,
                        trial_ends_at,
                        current_period_start,
                        current_period_end,
                        stripe_customer_id,
                        stripe_subscription_id,
                        stripe_price_id,
                        payment_status,
                        1 if cancel_at_period_end else 0,
                        payment_status,
                        payment_status,
                        grace_period_ends_at,
                        last_invoice_id,
                        event_created,
                        event_id,
                        status,
                        int(existing["id"]),
                    ),
                )
                subscription_id = int(existing["id"])
            else:
                cursor.execute(
                    """
                    INSERT INTO billing_subscriptions (
                        company_id,
                        plan_key,
                        status,
                        billing_cycle,
                        trial_ends_at,
                        current_period_start,
                        current_period_end,
                        stripe_customer_id,
                        stripe_subscription_id,
                        stripe_price_id,
                        gateway,
                        payment_status,
                        cancel_at_period_end,
                        grace_period_ends_at,
                        last_invoice_id,
                        gateway_event_created,
                        gateway_event_id,
                        gateway_updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stripe', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        company_id,
                        plan_key,
                        status,
                        billing_cycle,
                        trial_ends_at,
                        current_period_start,
                        current_period_end,
                        stripe_customer_id,
                        stripe_subscription_id,
                        stripe_price_id,
                        payment_status,
                        1 if cancel_at_period_end else 0,
                        grace_period_ends_at,
                        last_invoice_id,
                        event_created,
                        event_id,
                    ),
                )
                subscription_id = int(cursor.lastrowid)
        return {
            "applied": True,
            "reason": "reconciled",
            "subscription_id": subscription_id,
        }

    def record_billing_usage(
        self,
        company_id: int,
        metric: str,
        period_start: str,
        period_end: str,
        quantity: int = 1,
        subscription_id: int | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO billing_usage_records (
                company_id, subscription_id, metric, quantity,
                period_start, period_end
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_id, subscription_id, metric, quantity,
             period_start, period_end),
        )
        record_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(record_id)

    def consume_billing_usage(
        self,
        *,
        company_id: int,
        subscription_id: int,
        metric: str,
        period_start: str,
        period_end: str,
        quantity: int,
        limit: int | None,
    ) -> dict[str, Any]:
        """Serialize limit check + usage insert on the subscription row."""
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE billing_subscriptions
                SET updated_at = updated_at
                WHERE id = ?
                  AND company_id = ?
                """,
                (subscription_id, company_id),
            )
            if cursor.rowcount != 1:
                return {"consumed": False, "reason": "subscription_not_found"}
            cursor.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM billing_usage_records
                WHERE company_id = ?
                  AND subscription_id = ?
                  AND metric = ?
                  AND period_start >= ?
                  AND period_end <= ?
                """,
                (
                    company_id,
                    subscription_id,
                    metric,
                    period_start,
                    period_end,
                ),
            )
            used = int(cursor.fetchone()["total"] or 0)
            if limit is not None and used + quantity > limit:
                return {
                    "consumed": False,
                    "reason": "limit_exceeded",
                    "used": used,
                    "limit": limit,
                }
            cursor.execute(
                """
                INSERT INTO billing_usage_records (
                    company_id,
                    subscription_id,
                    metric,
                    quantity,
                    period_start,
                    period_end
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    subscription_id,
                    metric,
                    quantity,
                    period_start,
                    period_end,
                ),
            )
            usage_record_id = int(cursor.lastrowid)
            return {
                "consumed": True,
                "reason": "recorded",
                "used": used + quantity,
                "limit": limit,
                "usage_record_id": usage_record_id,
            }

    def refund_billing_usage(
        self,
        *,
        usage_record_id: int,
        company_id: int,
    ) -> bool:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                DELETE FROM billing_usage_records
                WHERE id = ?
                  AND company_id = ?
                """,
                (usage_record_id, company_id),
            )
            return cursor.rowcount == 1

    def get_billing_usage_totals(
        self,
        company_id: int,
        period_start: str,
        period_end: str,
    ) -> dict[str, int]:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT metric, SUM(quantity) AS total
            FROM billing_usage_records
            WHERE company_id = ?
              AND period_start >= ?
              AND period_end <= ?
            GROUP BY metric
            """,
            (company_id, period_start, period_end),
        )
        rows = cursor.fetchall()
        connection.close()
        return {str(row["metric"]): int(row["total"]) for row in rows}

    def get_billing_usage_daily(
        self,
        company_id: int,
        period_start: str,
        period_end: str,
        metric: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        backend = get_database_backend()
        if backend == "postgres":
            day_extract = "TO_CHAR(recorded_at, 'YYYY-MM-DD')"
        else:
            day_extract = "strftime('%Y-%m-%d', recorded_at)"

        metric_filter = ""
        params: list[Any] = [company_id, period_start, period_end]
        if metric:
            metric_filter = " AND metric = ?"
            params.append(metric)

        cursor.execute(
            f"""
            SELECT {day_extract} AS day, metric, SUM(quantity) AS total
            FROM billing_usage_records
            WHERE company_id = ?
              AND period_start >= ?
              AND period_end <= ?
              {metric_filter}
            GROUP BY day, metric
            ORDER BY day ASC
            """,
            tuple(params),
        )
        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]


def _guard_connections(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a repository method inside a connection scope."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with connection_scope():
            return method(*args, **kwargs)

    wrapper.__connection_guarded__ = True  # type: ignore[attr-defined]
    return wrapper


def _install_connection_guard(repository_class: type) -> None:
    """Guarantee connection release for every public repository method.

    Most repository methods release their connection on the success path only.
    This guard makes the release unconditional: whatever happens inside the
    call — a failed ``execute``, ``fetchall``, ``commit`` or serialization — the
    connections acquired during it are rolled back and returned to the pool
    exactly once. ``close()`` is idempotent, so a method that already closed its
    own connection is unaffected, and transaction boundaries are untouched.
    """
    for attribute_name, attribute in list(vars(repository_class).items()):
        if attribute_name.startswith("_") or not inspect.isfunction(attribute):
            continue
        if getattr(attribute, "__connection_guarded__", False):
            continue
        setattr(repository_class, attribute_name, _guard_connections(attribute))


_install_connection_guard(RuntimeRepository)
