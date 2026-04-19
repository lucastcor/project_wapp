import json
from typing import Any

from app.storage.db import get_connection, get_database_backend


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


class RuntimeRepository:
    def get_state(self) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("SELECT COUNT(*) AS count FROM messages")
        messages_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) AS count FROM contact_company_profiles")
        contacts_count = cursor.fetchone()["count"]

        connection.close()

        return {
            "messages_processed": messages_count,
            "contacts_count": contacts_count,
        }

    def get_observability_summary(self) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        def fetch_count(query: str) -> int:
            cursor.execute(query)
            row = cursor.fetchone()
            return int(row["count"]) if row else 0

        def fetch_grouped_counts(query: str) -> dict[str, int]:
            cursor.execute(query)
            rows = cursor.fetchall()
            result: dict[str, int] = {}
            for row in rows:
                group_key = row["group_key"] if row["group_key"] is not None else "unknown"
                result[str(group_key)] = int(row["count"])
            return result

        summary = {
            "totals": {
                "messages": fetch_count("SELECT COUNT(*) AS count FROM messages"),
                "contacts": fetch_count("SELECT COUNT(*) AS count FROM contact_company_profiles"),
                "pending_questions": fetch_count("SELECT COUNT(*) AS count FROM pending_questions"),
                "knowledge_entries": fetch_count("SELECT COUNT(*) AS count FROM knowledge_entries"),
                "message_examples": fetch_count("SELECT COUNT(*) AS count FROM message_examples"),
                "outbound_messages": fetch_count("SELECT COUNT(*) AS count FROM outbound_messages"),
                "finetuning_jobs": fetch_count("SELECT COUNT(*) AS count FROM finetuning_jobs"),
                "companies": fetch_count("SELECT COUNT(*) AS count FROM companies"),
                "audit_events": fetch_count("SELECT COUNT(*) AS count FROM audit_events"),
            },
            "pending_questions_by_status": fetch_grouped_counts(
                """
                SELECT status AS group_key, COUNT(*) AS count
                FROM pending_questions
                GROUP BY status
                """
            ),
            "outbound_by_status": fetch_grouped_counts(
                """
                SELECT status AS group_key, COUNT(*) AS count
                FROM outbound_messages
                GROUP BY status
                """
            ),
            "knowledge_by_status": fetch_grouped_counts(
                """
                SELECT status AS group_key, COUNT(*) AS count
                FROM knowledge_entries
                GROUP BY status
                """
            ),
            "messages_by_classification": fetch_grouped_counts(
                """
                SELECT classification AS group_key, COUNT(*) AS count
                FROM messages
                GROUP BY classification
                """
            ),
            "messages_by_action": fetch_grouped_counts(
                """
                SELECT action AS group_key, COUNT(*) AS count
                FROM messages
                GROUP BY action
                """
            ),
            "messages_by_reply_source": fetch_grouped_counts(
                """
                SELECT reply_source AS group_key, COUNT(*) AS count
                FROM messages
                GROUP BY reply_source
                """
            ),
            "companies_by_status": {
                "active": fetch_count("SELECT COUNT(*) AS count FROM companies WHERE active = 1"),
                "inactive": fetch_count("SELECT COUNT(*) AS count FROM companies WHERE active = 0"),
            },
            "audit_events_by_status": fetch_grouped_counts(
                """
                SELECT status AS group_key, COUNT(*) AS count
                FROM audit_events
                GROUP BY status
                """
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

        query = """
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
            WHERE (? IS NULL OR action = ?)
              AND (? IS NULL OR resource_type = ?)
              AND (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            LIMIT ?
        """
        cursor.execute(
            query,
            (action, action, resource_type, resource_type, company_id, company_id, limit),
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
                slug in PLACEHOLDER_COMPANY_SLUGS
                or name in PLACEHOLDER_COMPANY_NAMES
                or slug.startswith("demo-")
                or slug.startswith("test-")
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
            cursor.execute(
                """
                UPDATE pending_questions
                SET
                    status = 'expired',
                    owner_answer = ?,
                    resolution_type = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                  AND (? IS NULL OR company_id = ?)
                """,
                (owner_answer, resolution_type, question_id, company_id, company_id),
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
            cursor.execute(
                """
                UPDATE outbound_messages
                SET status = ?
                WHERE id = ?
                  AND status = 'pending'
                  AND (? IS NULL OR company_id = ?)
                """,
                (cancelled_status, outbound_message_id, company_id, company_id),
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
            cursor.execute(
                """
                UPDATE finetuning_jobs
                SET status = ?
                WHERE id = ?
                  AND status = 'failed'
                  AND (? IS NULL OR company_id = ?)
                """,
                (archived_status, finetuning_job_id, company_id, company_id),
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
    ) -> None:
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

        connection.commit()
        connection.close()

    def get_messages(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            """,
            (company_id, company_id),
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

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

    def get_pending_questions(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            """,
            (company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (question_id, company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (
                owner_answer,
                resolution_type,
                customer_followup_question,
                int(applies_globally),
                knowledge_action,
                question_id,
                company_id,
                company_id,
            ),
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

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            """,
            (company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (knowledge_entry_id, company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            ORDER BY applies_globally DESC, id DESC
            """,
            (company_id, company_id),
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
        connection = get_connection()
        cursor = connection.cursor()

        if contact_id:
            cursor.execute(
                """
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
                  AND (
                        (applies_globally = 1 AND (? IS NULL OR company_id = ?))
                        OR contact_id = ?
                      )
                ORDER BY applies_globally DESC, id DESC
                LIMIT ?
                """,
                (company_id, company_id, contact_id, limit),
            )
        else:
            cursor.execute(
                """
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
                  AND applies_globally = 1
                  AND (? IS NULL OR company_id = ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (company_id, company_id, limit),
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
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO message_examples (
                company_id,
                profile_name,
                scenario_type,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                used_for_training,
                active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                profile_name,
                scenario_type,
                customer_message,
                approved_reply,
                style_notes,
                quality_score,
                int(used_for_training),
                int(active),
            ),
        )

        message_example_id = cursor.lastrowid
        connection.commit()
        connection.close()

        return int(message_example_id)

    def get_message_examples(
        self,
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            """,
            (company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (message_example_id, company_id, company_id),
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
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        if profile_name and scenario_type:
            cursor.execute(
                """
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
                  AND (? IS NULL OR company_id = ?)
                  AND profile_name = ?
                  AND scenario_type = ?
                ORDER BY quality_score DESC, id DESC
                LIMIT ?
                """,
                (company_id, company_id, profile_name, scenario_type, limit),
            )
        elif profile_name:
            cursor.execute(
                """
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
                  AND (? IS NULL OR company_id = ?)
                  AND profile_name = ?
                ORDER BY quality_score DESC, id DESC
                LIMIT ?
                """,
                (company_id, company_id, profile_name, limit),
            )
        elif scenario_type:
            cursor.execute(
                """
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
                  AND (? IS NULL OR company_id = ?)
                  AND scenario_type = ?
                ORDER BY quality_score DESC, id DESC
                LIMIT ?
                """,
                (company_id, company_id, scenario_type, limit),
            )
        else:
            cursor.execute(
                """
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
                  AND (? IS NULL OR company_id = ?)
                ORDER BY quality_score DESC, id DESC
                LIMIT ?
                """,
                (company_id, company_id, limit),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
              AND used_for_training = 1
            ORDER BY quality_score DESC, id DESC
            """,
            (company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
              AND status = 'resolved'
              AND resolution_type = 'needs_customer_clarification'
              AND customer_followup_question IS NOT NULL
              AND TRIM(customer_followup_question) != ''
              AND clarification_consumed = 0
            ORDER BY resolved_at DESC, id DESC
            LIMIT 1
            """,
            (contact_id, company_id, company_id),
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
        cursor.execute(
            """
            UPDATE inbound_message_receipts
            SET
                status = 'completed',
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = ?
              AND provider_message_id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (provider.strip().lower(), provider_message_id.strip(), company_id, company_id),
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
        cursor.execute(
            """
            UPDATE inbound_message_receipts
            SET
                status = 'failed',
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = ?
              AND provider_message_id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (error, provider.strip().lower(), provider_message_id.strip(), company_id, company_id),
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

        cursor.execute(
            """
            UPDATE pending_questions
            SET clarification_consumed = 1
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (question_id, company_id, company_id),
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
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (contact_id, company_id, pending_question_id, message, source, status),
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

        cursor.execute(
            """
            SELECT
                id,
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                created_at,
                sent_at
            FROM outbound_messages
            WHERE (? IS NULL OR company_id = ?)
            ORDER BY id DESC
            """,
            (company_id, company_id),
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

        cursor.execute(
            """
            SELECT
                id,
                contact_id,
                company_id,
                pending_question_id,
                message,
                source,
                status,
                created_at,
                sent_at
            FROM outbound_messages
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (outbound_message_id, company_id, company_id),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def mark_outbound_message_sent(
        self,
        outbound_message_id: int,
        *,
        company_id: int | None = None,
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE outbound_messages
            SET
                status = 'sent',
                sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (outbound_message_id, company_id, company_id),
        )

        connection.commit()
        connection.close()

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
            cursor.execute(
                """
                UPDATE finetuning_jobs
                SET
                    status = ?,
                    fine_tuned_model = ?,
                    provider_error_code = ?,
                    provider_error_message = ?
                WHERE job_id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (
                    status,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    job_id,
                    company_id,
                    company_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE finetuning_jobs
                SET
                    status = ?,
                    fine_tuned_model = ?,
                    provider_error_code = ?,
                    provider_error_message = ?,
                    finished_at = ?
                WHERE job_id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (
                    status,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    finished_at,
                    job_id,
                    company_id,
                    company_id,
                ),
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
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (integration_id, company_id, company_id),
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
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO appointment_bookings (
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
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
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
            ),
        )

        booking_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return int(booking_id)

    def get_appointment_bookings(
        self,
        company_id: int | None = None,
        status: str | None = None,
        contact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
                updated_at
            FROM appointment_bookings
            WHERE (? IS NULL OR company_id = ?)
              AND (? IS NULL OR contact_id = ?)
              AND (? IS NULL OR status = ?)
            ORDER BY starts_at ASC, id ASC
            """,
            (company_id, company_id, contact_id, contact_id, status, status),
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

        cursor.execute(
            """
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
                updated_at
            FROM appointment_bookings
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (booking_id, company_id, company_id),
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

        cursor.execute(
            """
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
                updated_at
            FROM appointment_bookings
            WHERE (? IS NULL OR company_id = ?)
              AND status IN ('confirmed', 'pending')
              AND starts_at < ?
              AND ends_at > ?
            ORDER BY starts_at ASC, id ASC
            """,
            (company_id, company_id, ends_at, starts_at),
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
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE appointment_bookings
            SET
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (status, booking_id, company_id, company_id),
        )

        connection.commit()
        connection.close()

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
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (
                starts_at,
                ends_at,
                timezone,
                external_event_id,
                scheduling_profile_key,
                customer_profile,
                title,
                description,
                booking_id,
                company_id,
                company_id,
            ),
        )

        connection.commit()
        connection.close()

    def get_active_conversation_flow(
        self,
        contact_id: str,
        company_id: int | None = None,
    ) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
              AND status = 'active'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (contact_id, company_id, company_id),
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
            cursor.execute(
                """
                UPDATE conversation_flows
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (status, flow_id, company_id, company_id),
            )
        else:
            cursor.execute(
                """
                UPDATE conversation_flows
                SET
                    status = ?,
                    state_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (
                    status,
                    json.dumps(state, ensure_ascii=False),
                    flow_id,
                    company_id,
                    company_id,
                ),
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

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
              AND (? IS NULL OR status = ?)
              AND (? IS NULL OR audience = ?)
            ORDER BY remind_at ASC, id ASC
            """,
            (company_id, company_id, status, status, audience, audience),
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

        cursor.execute(
            """
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
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (reminder_id, company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            ORDER BY remind_at ASC, id ASC
            """,
            (reference_time, company_id, company_id),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        audience: str = "customer",
        *,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            ORDER BY remind_at ASC, id ASC
            """,
            (appointment_booking_id, audience, company_id, company_id),
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

        cursor.execute(
            """
            UPDATE customer_reminders
            SET
                status = 'sent',
                outbound_message_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (outbound_message_id, reminder_id, company_id, company_id),
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

        cursor.execute(
            """
            UPDATE customer_reminders
            SET
                status = 'cancelled',
                updated_at = CURRENT_TIMESTAMP
            WHERE appointment_booking_id = ?
              AND status = 'pending'
              AND (? IS NULL OR company_id = ?)
            """,
            (appointment_booking_id, company_id, company_id),
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

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
              AND (? IS NULL OR status = ?)
            ORDER BY priority_score DESC, created_at ASC, id ASC
            """,
            (company_id, company_id, status, status),
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
            cursor.execute(
                """
                UPDATE appointment_queue_entries
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (status, entry_id, company_id, company_id),
            )
        else:
            cursor.execute(
                """
                UPDATE appointment_queue_entries
                SET
                    status = ?,
                    notes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR company_id = ?)
                """,
                (status, notes, entry_id, company_id, company_id),
            )

        connection.commit()
        connection.close()

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

        cursor.execute(
            """
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
            WHERE (? IS NULL OR company_id = ?)
              AND (? IS NULL OR status = ?)
              AND (? IS NULL OR released_booking_id = ?)
              AND (? IS NULL OR queue_entry_id = ?)
            ORDER BY created_at ASC, id ASC
            """,
            (
                company_id,
                company_id,
                status,
                status,
                released_booking_id,
                released_booking_id,
                queue_entry_id,
                queue_entry_id,
            ),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (offer_id, company_id, company_id),
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

        cursor.execute(
            """
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
              AND (? IS NULL OR company_id = ?)
            """,
            (
                status,
                outbound_message_id,
                new_booking_id,
                response_deadline_at,
                follow_up_after,
                responded_at,
                response_notes,
                offer_id,
                company_id,
                company_id,
            ),
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
        cursor.execute(
            """
            UPDATE appointment_bookings
            SET booking_outcome = ?,
                booking_outcome_at = CURRENT_TIMESTAMP,
                booking_outcome_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (outcome, notes, booking_id, company_id, company_id),
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
        cursor.execute(
            """
            UPDATE appointment_bookings
            SET cancellation_reason = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            (reason, booking_id, company_id, company_id),
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

        cancel_conditions = list(conditions) + ["status = 'cancelled'", "cancellation_reason IS NOT NULL", "cancellation_reason != ''"]
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
        if backend == "postgres":
            month_extract = "TO_CHAR(starts_at, 'YYYY-MM')"
        else:
            month_extract = "strftime('%Y-%m', starts_at)"

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
            tuple([f"-{months} months"] + params),
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
                    limits_json = ?, features_json = ?, display_order = ?, active = 1
                WHERE plan_key = ?
                """,
                (name, price_monthly_cents, price_annual_monthly_cents,
                 limits_json, features_json, display_order, plan_key),
            )
            plan_id = int(existing["id"])
        else:
            cursor.execute(
                """
                INSERT INTO billing_plans (plan_key, name, price_monthly_cents,
                    price_annual_monthly_cents, limits_json, features_json, display_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_key, name, price_monthly_cents, price_annual_monthly_cents,
                 limits_json, features_json, display_order),
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

    def get_billing_plan(self, plan_key: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM billing_plans WHERE plan_key = ?", (plan_key,))
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
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO billing_subscriptions (
                company_id, plan_key, status, billing_cycle,
                current_period_start, current_period_end, trial_ends_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, plan_key, status, billing_cycle,
             current_period_start, current_period_end, trial_ends_at),
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
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [subscription_id, company_id, company_id]

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE billing_subscriptions
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (? IS NULL OR company_id = ?)
            """,
            tuple(params),
        )
        connection.commit()
        connection.close()

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
