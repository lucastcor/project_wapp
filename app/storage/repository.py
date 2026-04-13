import json
from typing import Any

from app.storage.db import get_connection


class RuntimeRepository:
    def get_state(self) -> dict[str, Any]:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("SELECT COUNT(*) AS count FROM messages")
        messages_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) AS count FROM contacts")
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
                "contacts": fetch_count("SELECT COUNT(*) AS count FROM contacts"),
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
                    f"SELECT COUNT(*) AS count FROM contacts{base_filter}",
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
        resource_id: str | None = None,
        company_id: int | None = None,
        status: str = "success",
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO audit_events (
                actor_id,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
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
    ) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id,
                actor_id,
                action,
                resource_type,
                resource_id,
                company_id,
                status,
                request_id,
                metadata_json,
                created_at
            FROM audit_events
            WHERE (? IS NULL OR action = ?)
              AND (? IS NULL OR resource_type = ?)
            ORDER BY id DESC
            LIMIT ?
        """
        cursor.execute(
            query,
            (action, action, resource_type, resource_type, limit),
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
            (contact_id, contact_name, company_id),
        )

        if contact_name or company_id is not None:
            cursor.execute(
                """
                UPDATE contacts
                SET
                    contact_name = COALESCE(?, contact_name),
                    company_id = COALESCE(?, company_id),
                    customer_profile = COALESCE(?, customer_profile),
                    notes = COALESCE(?, notes)
                WHERE contact_id = ?
                """,
                (contact_name, company_id, customer_profile, notes, contact_id),
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

    def get_messages(self) -> list[dict[str, Any]]:
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
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_contact(self, contact_id: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

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
    ) -> None:
        connection = get_connection()
        cursor = connection.cursor()

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

        connection.commit()
        connection.close()

    def get_contacts(self) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

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
            FROM contacts
            ORDER BY contact_id
            """
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
            FROM contacts
            WHERE (? IS NULL OR company_id = ?)
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

        cursor.execute(
            """
            UPDATE contacts
            SET
                contact_name = COALESCE(?, contact_name),
                company_id = COALESCE(?, company_id),
                customer_profile = COALESCE(?, customer_profile),
                notes = COALESCE(?, notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE contact_id = ?
            """,
            (contact_name, company_id, customer_profile, notes, contact_id),
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

    def get_pending_questions(self) -> list[dict[str, Any]]:
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
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_pending_question(self, question_id: int) -> dict[str, Any] | None:
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
            """,
            (question_id,),
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
            """,
            (
                owner_answer,
                resolution_type,
                customer_followup_question,
                int(applies_globally),
                knowledge_action,
                question_id,
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

    def get_knowledge_entries(self) -> list[dict[str, Any]]:
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
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_knowledge_entry(self, knowledge_entry_id: int) -> dict[str, Any] | None:
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
            """,
            (knowledge_entry_id,),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_active_knowledge_entries(self) -> list[dict[str, Any]]:
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
            ORDER BY applies_globally DESC, id DESC
            """
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

    def get_message_examples(self) -> list[dict[str, Any]]:
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
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_message_example(self, message_example_id: int) -> dict[str, Any] | None:
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
            """,
            (message_example_id,),
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

    def get_latest_customer_clarification(self, contact_id: str) -> dict[str, Any] | None:
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
              AND status = 'resolved'
              AND resolution_type = 'needs_customer_clarification'
              AND customer_followup_question IS NOT NULL
              AND TRIM(customer_followup_question) != ''
              AND clarification_consumed = 0
            ORDER BY resolved_at DESC, id DESC
            LIMIT 1
            """,
            (contact_id,),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def mark_clarification_consumed(self, question_id: int) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE pending_questions
            SET clarification_consumed = 1
            WHERE id = ?
            """,
            (question_id,),
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

    def get_outbound_messages(self) -> list[dict[str, Any]]:
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
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

    def get_outbound_message(self, outbound_message_id: int) -> dict[str, Any] | None:
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
            """,
            (outbound_message_id,),
        )

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def mark_outbound_message_sent(self, outbound_message_id: int) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE outbound_messages
            SET
                status = 'sent',
                sent_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (outbound_message_id,),
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
                """,
                (
                    status,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    job_id,
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
                """,
                (
                    status,
                    fine_tuned_model,
                    provider_error_code,
                    provider_error_message,
                    finished_at,
                    job_id,
                ),
            )

        connection.commit()
        connection.close()

    def get_finetuning_job_record(self, job_id: str) -> dict[str, Any] | None:
        connection = get_connection()
        cursor = connection.cursor()

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

        row = cursor.fetchone()
        connection.close()

        return dict(row) if row else None

    def get_finetuning_job_records(self) -> list[dict[str, Any]]:
        connection = get_connection()
        cursor = connection.cursor()

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

    def get_calendar_integration(self, integration_id: int) -> dict[str, Any] | None:
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
            """,
            (integration_id,),
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

    def get_appointment_booking(self, booking_id: int) -> dict[str, Any] | None:
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
            """,
            (booking_id,),
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

    def update_appointment_booking_status(self, booking_id: int, status: str) -> None:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE appointment_bookings
            SET
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, booking_id),
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
                """,
                (status, flow_id),
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
                """,
                (
                    status,
                    json.dumps(state, ensure_ascii=False),
                    flow_id,
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

    def get_customer_reminder(self, reminder_id: int) -> dict[str, Any] | None:
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
            """,
            (reminder_id,),
        )

        row = cursor.fetchone()
        connection.close()
        return dict(row) if row else None

    def get_due_customer_reminders(self, reference_time: str) -> list[dict[str, Any]]:
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
            ORDER BY remind_at ASC, id ASC
            """,
            (reference_time,),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def get_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        audience: str = "customer",
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
            ORDER BY remind_at ASC, id ASC
            """,
            (appointment_booking_id, audience),
        )

        rows = cursor.fetchall()
        connection.close()
        return [dict(row) for row in rows]

    def mark_customer_reminder_sent(
        self,
        reminder_id: int,
        *,
        outbound_message_id: int | None = None,
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
            """,
            (outbound_message_id, reminder_id),
        )

        connection.commit()
        connection.close()

    def cancel_customer_reminders_for_booking(self, appointment_booking_id: int) -> int:
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
            """,
            (appointment_booking_id,),
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

    def get_appointment_queue_entry(self, entry_id: int) -> dict[str, Any] | None:
        entries = self.get_appointment_queue_entries()
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
                """,
                (status, entry_id),
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
                """,
                (status, notes, entry_id),
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

    def get_appointment_queue_offer(self, offer_id: int) -> dict[str, Any] | None:
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
            """,
            (offer_id,),
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
            ),
        )

        connection.commit()
        connection.close()
