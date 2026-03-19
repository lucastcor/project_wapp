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

    def save_contact(
        self,
        contact_id: str,
        contact_name: str | None,
        company_id: int | None = None,
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
                    company_id = COALESCE(?, company_id)
                WHERE contact_id = ?
                """,
                (contact_name, company_id, contact_id),
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
                updated_at
            FROM contacts
            ORDER BY contact_id
            """
        )

        rows = cursor.fetchall()
        connection.close()

        return [dict(row) for row in rows]

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