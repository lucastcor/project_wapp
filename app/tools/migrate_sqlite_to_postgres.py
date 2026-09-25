import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.storage.db import (
    close_postgres_pool,
    db_session,
    get_database_backend,
    init_db,
)
from app.tools.portability_security import sanitize_runtime_row

TABLE_COLUMNS: list[tuple[str, list[str], str | None]] = [
    (
        "companies",
        ["id", "slug", "name", "business_type", "active", "created_at", "updated_at"],
        "id",
    ),
    (
        "saas_users",
        [
            "id",
            "email",
            "email_normalized",
            "display_name",
            "password_hash",
            "active",
            "email_verified_at",
            "last_login_at",
            "created_at",
            "updated_at",
            "auth_epoch",
        ],
        "id",
    ),
    (
        "company_memberships",
        [
            "id",
            "company_id",
            "user_id",
            "role",
            "active",
            "invited_by_user_id",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "user_invitations",
        [
            "id",
            "company_id",
            "email",
            "email_normalized",
            "role",
            "token_hash",
            "invited_by_user_id",
            "expires_at",
            "accepted_at",
            "revoked_at",
            "created_at",
        ],
        "id",
    ),
    (
        "user_sessions",
        [
            "id",
            "user_id",
            "token_hash",
            "csrf_token_hash",
            "expires_at",
            "created_at",
            "last_seen_at",
            "revoked_at",
            "user_agent",
            "ip_address",
            "auth_epoch",
        ],
        "id",
    ),
    (
        "password_reset_tokens",
        [
            "id",
            "user_id",
            "token_hash",
            "expires_at",
            "consumed_at",
            "created_at",
        ],
        "id",
    ),
    (
        "company_contexts",
        [
            "id",
            "company_id",
            "company_slug",
            "context_json",
            "schema_version",
            "revision",
            "created_at",
            "updated_at",
            "updated_by",
        ],
        "id",
    ),
    (
        "whatsapp_channel_bindings",
        [
            "id",
            "company_id",
            "phone_number_id",
            "display_phone_number",
            "active",
            "created_at",
            "updated_at",
            "updated_by",
        ],
        "id",
    ),
    (
        "contacts",
        [
            "contact_id",
            "contact_name",
            "company_id",
            "last_classification",
            "last_interaction_at",
            "conversation_summary",
            "pipeline_stage",
            "next_action",
            "lead_temperature",
            "customer_profile",
            "notes",
            "updated_at",
        ],
        None,
    ),
    (
        "contact_company_profiles",
        [
            "contact_id",
            "company_id",
            "contact_name",
            "last_classification",
            "last_interaction_at",
            "conversation_summary",
            "pipeline_stage",
            "next_action",
            "lead_temperature",
            "customer_profile",
            "notes",
            "updated_at",
        ],
        None,
    ),
    (
        "messages",
        [
            "id",
            "contact_id",
            "company_id",
            "message",
            "classification",
            "classification_reason",
            "classification_confidence",
            "reply",
            "auto_reply",
            "created_at",
            "reply_source",
            "action",
            "priority",
            "retrieval_json",
        ],
        "id",
    ),
    (
        "pending_questions",
        [
            "id",
            "contact_id",
            "company_id",
            "customer_message",
            "question_type",
            "question_for_owner",
            "status",
            "owner_answer",
            "resolution_type",
            "customer_followup_question",
            "clarification_consumed",
            "applies_globally",
            "knowledge_action",
            "created_at",
            "resolved_at",
        ],
        "id",
    ),
    (
        "knowledge_entries",
        [
            "id",
            "source_question_id",
            "contact_id",
            "company_id",
            "title",
            "content",
            "knowledge_type",
            "applies_globally",
            "status",
            "created_at",
        ],
        "id",
    ),
    (
        "message_examples",
        [
            "id",
            "company_id",
            "profile_name",
            "scenario_type",
            "customer_message",
            "approved_reply",
            "style_notes",
            "quality_score",
            "used_for_training",
            "active",
            "created_at",
            "customer_profile",
            "approval_status",
            "source_type",
            "source_reference",
            "pii_scrubbed",
            "source_group_key",
            "conversation_context_json",
            "source_batch_id",
            "source_conversation_id",
            "source_candidate_id",
            "approved_by",
            "approved_at",
        ],
        "id",
    ),
    (
        "outbound_messages",
        [
            "id",
            "contact_id",
            "company_id",
            "pending_question_id",
            "message",
            "source",
            "status",
            "channel",
            "attempt_count",
            "max_attempts",
            "next_attempt_at",
            "claimed_at",
            "claim_token",
            "last_error",
            "provider_message_id",
            "provider_response_json",
            "failed_at",
            "created_at",
            "sent_at",
            "template_parameters_json",
            "delivery_status",
            "delivery_updated_at",
        ],
        "id",
    ),
    (
        "worker_leases",
        [
            "lease_name",
            "owner_token",
            "expires_at",
            "updated_at",
        ],
        None,
    ),
    (
        "finetuning_jobs",
        [
            "id",
            "job_id",
            "company_id",
            "status",
            "base_model",
            "suffix",
            "training_file_id",
            "validation_file_id",
            "fine_tuned_model",
            "provider_error_code",
            "provider_error_message",
            "created_at",
            "finished_at",
            "dataset_version_id",
            "readiness_json",
            "requested_by",
        ],
        "id",
    ),
    (
        "audit_events",
        [
            "id",
            "actor_id",
            "admin_role",
            "auth_mode",
            "action",
            "resource_type",
            "resource_id",
            "company_id",
            "status",
            "request_id",
            "http_method",
            "http_path",
            "client_ip",
            "user_agent",
            "metadata_json",
            "created_at",
        ],
        "id",
    ),
    (
        "calendar_integrations",
        [
            "id",
            "company_id",
            "provider",
            "external_calendar_id",
            "config_json",
            "active",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    # Recurso antes do agendamento que o referencia (070).
    (
        "booking_resources",
        [
            "id",
            "company_id",
            "resource_key",
            "label",
            "capacity",
            "active",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "appointment_bookings",
        [
            "id",
            "company_id",
            "contact_id",
            "integration_id",
            "resource_id",
            "hold_starts_at",
            "hold_ends_at",
            "provider",
            "external_event_id",
            "scheduling_profile_key",
            "customer_profile",
            "title",
            "description",
            "starts_at",
            "ends_at",
            "timezone",
            "status",
            "booking_outcome",
            "booking_outcome_at",
            "booking_outcome_notes",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "conversation_flows",
        [
            "id",
            "company_id",
            "contact_id",
            "flow_type",
            "status",
            "state_json",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "customer_reminders",
        [
            "id",
            "company_id",
            "contact_id",
            "reminder_text",
            "reminder_summary",
            "remind_at",
            "timezone",
            "status",
            "outbound_message_id",
            "audience",
            "source",
            "appointment_booking_id",
            "attempt_count",
            "max_attempts",
            "next_attempt_at",
            "claimed_at",
            "claim_token",
            "last_error",
            "failed_at",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "appointment_queue_entries",
        [
            "id",
            "company_id",
            "contact_id",
            "desired_title",
            "scheduling_profile_key",
            "customer_profile",
            "request_summary",
            "timezone",
            "earliest_starts_at",
            "latest_starts_at",
            "preferred_periods_json",
            "priority_tier",
            "priority_score",
            "accepts_short_notice",
            "original_booking_id",
            "status",
            "source",
            "notes",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "appointment_queue_offers",
        [
            "id",
            "company_id",
            "queue_entry_id",
            "released_booking_id",
            "outbound_message_id",
            "new_booking_id",
            "offered_starts_at",
            "offered_ends_at",
            "timezone",
            "status",
            "response_deadline_at",
            "follow_up_after",
            "responded_at",
            "response_notes",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "billing_plans",
        [
            "id",
            "plan_key",
            "name",
            "price_monthly_cents",
            "price_annual_monthly_cents",
            "limits_json",
            "features_json",
            "display_order",
            "active",
            "currency",
            "stripe_product_id",
            "stripe_monthly_price_id",
            "stripe_annual_price_id",
            "created_at",
        ],
        "id",
    ),
    (
        "billing_subscriptions",
        [
            "id",
            "company_id",
            "plan_key",
            "status",
            "billing_cycle",
            "trial_ends_at",
            "current_period_start",
            "current_period_end",
            "stripe_customer_id",
            "stripe_subscription_id",
            "gateway",
            "stripe_price_id",
            "payment_status",
            "cancel_at_period_end",
            "grace_period_ends_at",
            "last_invoice_id",
            "gateway_event_created",
            "gateway_event_id",
            "gateway_updated_at",
            "cancelled_at",
            "cancel_reason",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "billing_webhook_events",
        [
            "id",
            "provider",
            "event_id",
            "event_type",
            "event_created",
            "livemode",
            "company_id",
            "payload_hash",
            "status",
            "attempt_count",
            "last_error",
            "processed_at",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    (
        "billing_usage_records",
        [
            "id",
            "company_id",
            "subscription_id",
            "metric",
            "quantity",
            "period_start",
            "period_end",
            "recorded_at",
        ],
        "id",
    ),
    (
        "inbound_message_receipts",
        [
            "id",
            "provider",
            "provider_message_id",
            "company_id",
            "contact_id",
            "payload_mode",
            "status",
            "last_error",
            "created_at",
            "updated_at",
        ],
        "id",
    ),
    ("inbound_jobs", ["id", "company_id", "provider_message_id", "contact_id", "payload_json", "status", "claim_token", "lease_expires_at", "result_json", "outbound_message_id", "last_error", "created_at", "updated_at", "available_at", "dispatch_attempts"], "id"),
    ("booking_operations", ["id", "company_id", "contact_id", "kind", "status", "source", "booking_id", "resource_key", "params_json", "params_hash", "target_version", "flow_id", "idempotency_key", "expires_at", "confirmed_at", "confirmation_ref", "started_at", "completed_at", "result_json", "last_error", "external_ref", "attempt_count", "created_at", "updated_at"], "id"),
    ("whatsapp_contact_states", ["company_id", "contact_id", "last_inbound_at", "opted_out_at", "opt_out_source", "updated_at"], None),
    ("contact_consents", ["id", "company_id", "contact_id", "purpose", "status", "source", "evidence_ref", "created_at", "updated_at"], "id"),
    ("whatsapp_templates", ["id", "company_id", "purpose", "name", "language_code", "category", "status", "parameter_count", "active", "created_at", "updated_at"], "id"),
    ("whatsapp_delivery_events", ["id", "company_id", "provider_message_id", "outbound_message_id", "status", "provider_timestamp", "error_code", "received_at"], "id"),
    ("conversation_ownerships", ["company_id", "contact_id", "owner", "owner_user_id", "reason", "version", "taken_at", "released_at", "updated_at"], None),
    ("worker_heartbeats", ["component", "instance_id", "expires_at", "updated_at"], None),
    # Plataforma multiempresa e Automation Studio (migrations 038-066).
    # Mesma ordem pai-antes-de-filho de BACKUP_TABLES: a copia insere
    # nesta ordem e o TRUNCATE a percorre invertida.
    (
        "service_accounts",
        [
            "id",
            "company_id",
            "slug",
            "display_name",
            "role",
            "description",
            "active",
            "created_by_user_id",
            "created_at",
            "updated_at",
            "auth_epoch",
        ],
        'id',
    ),
    (
        "service_account_tokens",
        [
            "id",
            "service_account_id",
            "company_id",
            "token_hash",
            "token_prefix",
            "description",
            "scopes_json",
            "active",
            "created_by_user_id",
            "created_at",
            "expires_at",
            "revoked_at",
            "last_used_at",
        ],
        'id',
    ),
    (
        "company_channel_bindings",
        [
            "id",
            "company_id",
            "provider",
            "channel_type",
            "external_id",
            "label",
            "active",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "company_context_versions",
        [
            "id",
            "company_id",
            "version",
            "status",
            "context_json",
            "capabilities_json",
            "policies_json",
            "vertical_pack",
            "content_hash",
            "author",
            "change_note",
            "created_at",
            "published_at",
        ],
        'id',
    ),
    (
        "knowledge_documents",
        [
            "id",
            "company_id",
            "external_ref",
            "title",
            "source_type",
            "document_type",
            "status",
            "priority",
            "valid_from",
            "valid_until",
            "current_version_id",
            "approved_by",
            "approved_at",
            "created_by",
            "created_at",
            "updated_at",
            "current_published_version_id",
            "working_version_id",
            "metadata_hash",
        ],
        'id',
    ),
    (
        "knowledge_document_versions",
        [
            "id",
            "company_id",
            "document_id",
            "version",
            "content",
            "content_hash",
            "status",
            "author",
            "created_at",
            "title",
            "document_type",
            "priority",
            "valid_from",
            "valid_until",
            "source_uri",
            "language",
        ],
        'id',
    ),
    (
        "knowledge_chunks",
        [
            "id",
            "company_id",
            "document_id",
            "document_version_id",
            "chunk_index",
            "content",
            "search_text",
            "token_estimate",
            "embedding_model",
            "embedding_json",
            "created_at",
        ],
        'id',
    ),
    (
        "conversation_import_batches",
        [
            "id",
            "company_id",
            "batch_key",
            "source_format",
            "source_label",
            "status",
            "dry_run",
            "stats_json",
            "error_detail",
            "created_by",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "imported_conversations",
        [
            "id",
            "company_id",
            "batch_id",
            "external_conversation_id",
            "contact_ref_hash",
            "session_index",
            "started_at",
            "ended_at",
            "message_count",
            "status",
            "created_at",
        ],
        'id',
    ),
    (
        "imported_messages",
        [
            "id",
            "company_id",
            "batch_id",
            "conversation_id",
            "sequence",
            "role",
            "anonymized_content",
            "pii_report_json",
            "occurred_at",
            "source_line",
            "created_at",
        ],
        'id',
    ),
    (
        "training_candidates",
        [
            "id",
            "company_id",
            "batch_id",
            "conversation_id",
            "candidate_hash",
            "group_key",
            "scenario_type",
            "customer_profile",
            "profile_name",
            "context_json",
            "customer_message",
            "approved_reply",
            "quality_score",
            "pii_report_json",
            "review_status",
            "review_notes",
            "reviewed_by",
            "reviewed_at",
            "promoted_message_example_id",
            "created_at",
            "updated_at",
            "promotion_state",
        ],
        'id',
    ),
    (
        "dataset_versions",
        [
            "id",
            "company_id",
            "version",
            "name",
            "status",
            "content_hash",
            "split_seed",
            "validation_ratio",
            "holdout_ratio",
            "stats_json",
            "readiness_json",
            "created_by",
            "frozen_by",
            "created_at",
            "frozen_at",
            "superseded_at",
        ],
        'id',
    ),
    (
        "dataset_version_items",
        [
            "id",
            "company_id",
            "dataset_version_id",
            "split",
            "group_key",
            "source_type",
            "source_id",
            "scenario_type",
            "customer_profile",
            "profile_name",
            "context_json",
            "customer_message",
            "approved_reply",
            "style_notes",
            "quality_score",
            "content_hash",
            "near_duplicate_key",
            "created_at",
        ],
        'id',
    ),
    (
        "evaluation_cases",
        [
            "id",
            "company_id",
            "case_key",
            "version",
            "scenario_type",
            "customer_profile",
            "customer_message",
            "context_json",
            "expected_behavior",
            "expected_keywords_json",
            "forbidden_keywords_json",
            "requires_handoff",
            "requires_source_grounding",
            "requires_action_tool",
            "active",
            "created_by",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "evaluation_runs",
        [
            "id",
            "company_id",
            "run_key",
            "approach",
            "model_ref",
            "dataset_version_id",
            "status",
            "total_cases",
            "passed_cases",
            "scores_json",
            "created_by",
            "created_at",
            "execution_mode",
            "promotion_eligible",
            "base_model",
            "finetuning_job_id",
            "prompt_contract_version",
            "retrieval_config_version",
            "case_suite_hash",
            "subject_fingerprint",
            "executor_kind",
            "provider",
            "attestation_json",
        ],
        'id',
    ),
    (
        "evaluation_results",
        [
            "id",
            "company_id",
            "run_id",
            "case_id",
            "passed",
            "dimension_scores_json",
            "failure_reasons_json",
            "response_excerpt",
            "sources_json",
            "created_at",
        ],
        'id',
    ),
    (
        "company_models",
        [
            "id",
            "company_id",
            "model_ref",
            "base_model",
            "dataset_version_id",
            "finetuning_job_id",
            "evaluation_run_id",
            "status",
            "notes",
            "activated_by",
            "activated_at",
            "retired_at",
            "created_by",
            "created_at",
            "updated_at",
            "job_status",
            "failure_reason",
            "promoted_evaluation_run_id",
        ],
        'id',
    ),
    (
        "automation_workflows",
        [
            "id",
            "company_id",
            "workflow_key",
            "name",
            "description",
            "archived",
            "created_by",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_workflow_versions",
        [
            "id",
            "company_id",
            "workflow_id",
            "version",
            "schema_version",
            "graph_json",
            "canonical_form",
            "content_hash",
            "trigger_type",
            "published_by",
            "published_at",
            "notes",
        ],
        'id',
    ),
    (
        "automation_workflow_drafts",
        [
            "id",
            "company_id",
            "workflow_id",
            "revision",
            "schema_version",
            "graph_json",
            "viewport_json",
            "updated_by",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_deployments",
        [
            "id",
            "company_id",
            "workflow_id",
            "version_id",
            "trigger_type",
            "state",
            "activated_by",
            "activated_at",
            "paused_by",
            "paused_at",
            "created_by",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_events",
        [
            "id",
            "company_id",
            "event_key",
            "event_type",
            "schema_version",
            "payload_json",
            "payload_bytes",
            "occurred_at",
            "created_at",
        ],
        'id',
    ),
    (
        "automation_outbox",
        [
            "id",
            "company_id",
            "event_id",
            "status",
            "attempts",
            "last_error",
            "available_at",
            "lease_owner",
            "lease_expires_at",
            "dispatched_at",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_executions",
        [
            "id",
            "company_id",
            "workflow_id",
            "version_id",
            "event_id",
            "enrollment_key",
            "status",
            "current_node_id",
            "steps_executed",
            "context_json",
            "failure_reason",
            "started_at",
            "finished_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_step_executions",
        [
            "id",
            "company_id",
            "execution_id",
            "sequence",
            "node_id",
            "node_type",
            "status",
            "attempt",
            "chosen_handle",
            "detail_json",
            "failure_reason",
            "started_at",
            "finished_at",
        ],
        'id',
    ),
    (
        "automation_jobs",
        [
            "id",
            "company_id",
            "execution_id",
            "job_kind",
            "node_id",
            "status",
            "attempts",
            "max_attempts",
            "run_at",
            "lease_owner",
            "lease_expires_at",
            "last_error",
            "payload_json",
            "cancel_requested",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
    (
        "automation_effects",
        [
            "id",
            "company_id",
            "execution_id",
            "effect_key",
            "effect_kind",
            "node_id",
            "occurrence",
            "status",
            "request_json",
            "result_json",
            "failure_reason",
            "created_at",
            "completed_at",
        ],
        'id',
    ),
    (
        "automation_approval_gates",
        [
            "id",
            "company_id",
            "execution_id",
            "node_id",
            "occurrence",
            "status",
            "title",
            "summary",
            "payload_json",
            "claimed_by",
            "claimed_at",
            "lease_expires_at",
            "decided_by",
            "decided_at",
            "decision",
            "decision_note",
            "expires_at",
            "created_at",
            "updated_at",
        ],
        'id',
    ),
]


def _quote_identifier(identifier: str) -> str:
    if (
        not identifier
        or not identifier.replace("_", "").isalnum()
        or identifier[0].isdigit()
    ):
        raise ValueError(f"Unsafe migration identifier: {identifier!r}")
    return f'"{identifier}"'


def _build_insert_statement(table: str, columns: Sequence[str]) -> str:
    table_sql = _quote_identifier(table)
    columns_sql = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {table_sql} ({columns_sql}) VALUES ({placeholders})"  # nosec B608


def _sqlite_table_exists(connection: sqlite3.Connection, table: str) -> bool:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table,),
    )
    return cursor.fetchone() is not None


def _sqlite_table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    cursor = connection.cursor()
    cursor.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    return {str(row["name"]) for row in cursor.fetchall()}


def _fetch_sqlite_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
) -> tuple[list[str], list[sqlite3.Row]]:
    available_columns = _sqlite_table_columns(connection, table)
    selected_columns = [column for column in columns if column in available_columns]
    if not selected_columns:
        return [], []
    cursor = connection.cursor()
    selected_sql = ", ".join(_quote_identifier(column) for column in selected_columns)
    select_sql = f"SELECT {selected_sql} FROM {_quote_identifier(table)}"  # nosec B608
    cursor.execute(select_sql)
    return selected_columns, cursor.fetchall()


def _reset_identity(cursor, table: str, id_column: str) -> None:
    table_sql = _quote_identifier(table)
    id_sql = _quote_identifier(id_column)
    reset_sql = f"""
        SELECT setval(
            pg_get_serial_sequence(%s, %s),
            COALESCE((SELECT MAX({id_sql}) FROM {table_sql}), 1),
            COALESCE((SELECT MAX({id_sql}) IS NOT NULL FROM {table_sql}), false)
        )
        """  # nosec B608
    cursor.execute(reset_sql, (table, id_column))


MIGRATION_TABLE_NAMES: list[str] = [
    table for table, _columns, _identity in TABLE_COLUMNS
]
# Owned by init_db() on the target, so it is deliberately not copied.
NON_MIGRATED_TABLES = {"schema_migrations"}


def _migration_manifest() -> dict[str, list[str]]:
    return {table: list(columns) for table, columns, _identity in TABLE_COLUMNS}


def _sqlite_schema(connection: sqlite3.Connection) -> dict[str, list[str]]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    table_names = [
        str(row[0] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in cursor.fetchall()
    ]
    schema: dict[str, list[str]] = {}
    for table_name in table_names:
        column_rows = connection.execute(
            f"PRAGMA table_info({_quote_identifier(table_name)})"
        ).fetchall()
        schema[table_name] = [
            str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
            for row in column_rows
        ]
    return schema


def _runtime_schema() -> dict[str, list[str]]:
    backend = get_database_backend()
    with db_session() as connection:
        cursor = connection.cursor()
        if backend == "postgres":
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                ORDER BY table_name, ordinal_position
                """
            )
            schema: dict[str, list[str]] = {}
            for row in cursor.fetchall():
                schema.setdefault(str(row["table_name"]), []).append(
                    str(row["column_name"])
                )
            return schema

        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        table_names = [str(row["name"]) for row in cursor.fetchall()]
        schema = {}
        for table_name in table_names:
            cursor.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
            schema[table_name] = [str(row["name"]) for row in cursor.fetchall()]
        return schema


def _schema_coverage_issues(
    schema: dict[str, list[str]],
    *,
    require_complete_manifest: bool,
) -> list[str]:
    manifest = _migration_manifest()
    expected_tables = set(manifest) | NON_MIGRATED_TABLES
    actual_tables = set(schema)
    issues = [
        f"unmapped table: {table_name}"
        for table_name in sorted(actual_tables - expected_tables)
    ]
    if require_complete_manifest:
        issues.extend(
            f"missing runtime table: {table_name}"
            for table_name in sorted(expected_tables - actual_tables)
        )

    for table_name in sorted(actual_tables & set(manifest)):
        actual_columns = set(schema[table_name])
        declared_columns = set(manifest[table_name])
        issues.extend(
            f"unmapped column: {table_name}.{column_name}"
            for column_name in sorted(actual_columns - declared_columns)
        )
        if require_complete_manifest:
            issues.extend(
                f"missing runtime column: {table_name}.{column_name}"
                for column_name in sorted(declared_columns - actual_columns)
            )
    return issues


def audit_migration_coverage(
    sqlite_connection: sqlite3.Connection | None = None,
    *,
    require_complete_manifest: bool = True,
) -> list[str]:
    """Tables and columns that would be silently lost by the migration."""
    schema = (
        _sqlite_schema(sqlite_connection)
        if sqlite_connection is not None
        else _runtime_schema()
    )
    return _schema_coverage_issues(
        schema,
        require_complete_manifest=require_complete_manifest,
    )


def migrate(
    sqlite_path: str,
    postgres_url: str,
    truncate_first: bool = True,
) -> dict[str, Any]:
    source_path = Path(sqlite_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {source_path}")

    original_database_url = settings.database_url
    original_postgres_database_url = settings.postgres_database_url

    close_postgres_pool()
    sqlite_connection: sqlite3.Connection | None = None
    try:
        sqlite_connection = sqlite3.connect(source_path)
        sqlite_connection.row_factory = sqlite3.Row
        source_issues = audit_migration_coverage(
            sqlite_connection,
            require_complete_manifest=False,
        )
        if source_issues:
            raise RuntimeError(
                "SQLite source schema contains data that the migration does not cover: "
                + "; ".join(source_issues)
            )

        settings.database_url = postgres_url
        settings.postgres_database_url = postgres_url
        init_db()
        target_issues = audit_migration_coverage()
        if target_issues:
            raise RuntimeError(
                "PostgreSQL target schema does not match the migration manifest: "
                + "; ".join(target_issues)
            )

        import psycopg  # type: ignore

        return _copy_tables(
            sqlite_connection,
            postgres_url,
            truncate_first=truncate_first,
            psycopg=psycopg,
        )
    finally:
        if sqlite_connection is not None:
            sqlite_connection.close()
        close_postgres_pool()
        settings.database_url = original_database_url
        settings.postgres_database_url = original_postgres_database_url


def _target_column_requirements(
    postgres_cursor: Any, table: str
) -> dict[str, dict[str, Any]]:
    postgres_cursor.execute(
        """
        SELECT
            column_name,
            is_nullable,
            column_default,
            is_identity
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return {
        str(row[0]): {
            "nullable": str(row[1]) == "YES",
            "default": row[2],
            "identity": str(row[3]) == "YES",
        }
        for row in postgres_cursor.fetchall()
    }


def _validate_legacy_source_defaults(
    postgres_cursor: Any,
    *,
    table: str,
    selected_columns: Sequence[str],
) -> None:
    requirements = _target_column_requirements(postgres_cursor, table)
    missing_columns = set(requirements) - set(selected_columns)
    unsafe_missing = [
        column_name
        for column_name in sorted(missing_columns)
        if not requirements[column_name]["nullable"]
        and requirements[column_name]["default"] is None
        and not requirements[column_name]["identity"]
    ]
    if unsafe_missing:
        raise RuntimeError(
            f"Legacy SQLite table {table!r} lacks required columns with no target default: "
            + ", ".join(unsafe_missing)
        )


def _copy_tables(
    sqlite_connection: sqlite3.Connection,
    postgres_url: str,
    *,
    truncate_first: bool,
    psycopg: Any,
) -> dict[str, Any]:
    table_counts: dict[str, int] = {
        table_name: 0 for table_name, _columns, _identity in TABLE_COLUMNS
    }
    sanitized_rows = 0
    sensitive_fields_removed = 0
    integrations_disabled = 0
    integration_ids_requiring_reconfiguration: list[int | str] = []

    with psycopg.connect(postgres_url) as postgres_connection:
        with postgres_connection.cursor() as postgres_cursor:
            source_has_contact_profiles = _sqlite_table_exists(
                sqlite_connection, "contact_company_profiles"
            )
            if truncate_first:
                ordered_tables = [table for table, _, _ in reversed(TABLE_COLUMNS)]
                ordered_sql = ", ".join(
                    _quote_identifier(table) for table in ordered_tables
                )
                postgres_cursor.execute(
                    f"TRUNCATE TABLE {ordered_sql} RESTART IDENTITY CASCADE"
                )

            for table, columns, identity_column in TABLE_COLUMNS:
                if not _sqlite_table_exists(sqlite_connection, table):
                    continue

                selected_columns, rows = _fetch_sqlite_rows(
                    sqlite_connection, table, columns
                )
                if not rows:
                    table_counts[table] = 0
                    continue
                _validate_legacy_source_defaults(
                    postgres_cursor,
                    table=table,
                    selected_columns=selected_columns,
                )

                sanitized_table_rows: list[dict[str, Any]] = []
                needs_active_column = False
                for row in rows:
                    sanitized, removed_count, integration_disabled = (
                        sanitize_runtime_row(
                            table,
                            dict(row),
                        )
                    )
                    if removed_count:
                        sanitized_rows += 1
                        sensitive_fields_removed += removed_count
                    if integration_disabled:
                        integrations_disabled += 1
                        integration_id = sanitized.get("id")
                        if isinstance(integration_id, (int, str)):
                            integration_ids_requiring_reconfiguration.append(
                                integration_id
                            )
                        needs_active_column = (
                            needs_active_column or "active" not in selected_columns
                        )
                    sanitized_table_rows.append(sanitized)

                if needs_active_column:
                    selected_columns.append("active")

                insert_sql = _build_insert_statement(table, selected_columns)
                values = [
                    tuple(row[column] for column in selected_columns)
                    for row in sanitized_table_rows
                ]
                postgres_cursor.executemany(insert_sql, values)
                table_counts[table] = len(values)

                if identity_column:
                    _reset_identity(postgres_cursor, table, identity_column)

            if not source_has_contact_profiles and _sqlite_table_exists(
                sqlite_connection, "contacts"
            ):
                postgres_cursor.execute(
                    """
                    INSERT INTO contact_company_profiles (
                        contact_id,
                        company_id,
                        contact_name,
                        last_classification,
                        last_interaction_at,
                        conversation_summary,
                        pipeline_stage,
                        next_action,
                        lead_temperature,
                        customer_profile,
                        notes,
                        updated_at
                    )
                    SELECT
                        contact_id,
                        company_id,
                        contact_name,
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
                    WHERE company_id IS NOT NULL
                    ON CONFLICT (contact_id, company_id) DO NOTHING
                    """
                )
                table_counts["contact_company_profiles"] = max(
                    0,
                    int(postgres_cursor.rowcount),
                )

    return {
        "table_counts": table_counts,
        "sanitization": {
            "rows_sanitized": sanitized_rows,
            "sensitive_fields_removed": sensitive_fields_removed,
            "integrations_disabled": integrations_disabled,
            "integration_ids_requiring_reconfiguration": (
                integration_ids_requiring_reconfiguration
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migra dados do SQLite local para Postgres."
    )
    parser.add_argument(
        "--sqlite-path",
        default="project_wapp.db",
        help="Caminho do banco SQLite de origem.",
    )
    parser.add_argument(
        "--postgres-url",
        default="",
        help="URL de destino Postgres. Se omitida, usa POSTGRES_DATABASE_URL ou DATABASE_URL.",
    )
    parser.add_argument(
        "--keep-target-data",
        action="store_true",
        help="Nao limpa as tabelas de destino antes de migrar.",
    )
    args = parser.parse_args()

    postgres_url = (
        args.postgres_url or settings.postgres_database_url or settings.database_url
    )
    if not postgres_url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("A URL de destino precisa ser Postgres.")

    result = migrate(
        sqlite_path=args.sqlite_path,
        postgres_url=postgres_url,
        truncate_first=not args.keep_target_data,
    )
    print(f"Migracao concluida com sucesso. Sanitizacao: {result['sanitization']}.")


if __name__ == "__main__":
    main()
