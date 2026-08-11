"""Migrations da plataforma SaaS multiempresa (038+).

Todas são aditivas, idempotentes e compatíveis com SQLite e Postgres.
Nenhuma remove ou renomeia dado existente.

Renumeradas de 024-037 para 038-051 na consolidação da Release 1: as
versões 024-037 já estavam ocupadas na linha principal por conteúdo
diferente (`024_company_contexts`, `029_human_identity`, ...). Como estas
migrations nunca foram implantadas — a branch de origem não foi mesclada e
não há release marcada — a renumeração é segura e não exige reparo de
nenhum banco existente. Ver `docs/migration_compatibility_map.md`.
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


# Tabelas com chave primária autoincremental: precisam de RETURNING id no Postgres.
PLATFORM_ID_TABLES = {
    "company_channel_bindings",
    "company_context_versions",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "conversation_import_batches",
    "imported_conversations",
    "imported_messages",
    "training_candidates",
    "dataset_versions",
    "dataset_version_items",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
    "company_models",
}


def _create_company_channel_bindings(cursor: Any) -> None:
    create_table(
        cursor,
        "company_channel_bindings",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            channel_type TEXT NOT NULL DEFAULT 'whatsapp',
            external_id TEXT NOT NULL,
            label TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_unique_index(
        cursor,
        "idx_channel_bindings_provider_external",
        "company_channel_bindings",
        "provider, external_id",
    )
    create_index(
        cursor,
        "idx_channel_bindings_company_active",
        "company_channel_bindings",
        "company_id, active",
    )


def _create_company_context_versions(cursor: Any) -> None:
    create_table(
        cursor,
        "company_context_versions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            context_json TEXT NOT NULL,
            capabilities_json TEXT NOT NULL DEFAULT '{}',
            policies_json TEXT NOT NULL DEFAULT '{}',
            vertical_pack TEXT,
            content_hash TEXT NOT NULL,
            author TEXT,
            change_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP
        """,
    )
    create_unique_index(
        cursor,
        "idx_company_context_versions_company_version",
        "company_context_versions",
        "company_id, version",
    )
    create_index(
        cursor,
        "idx_company_context_versions_company_status",
        "company_context_versions",
        "company_id, status, version",
    )


def _create_knowledge_tables(cursor: Any) -> None:
    create_table(
        cursor,
        "knowledge_documents",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            external_ref TEXT,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'manual',
            document_type TEXT NOT NULL DEFAULT 'general_faq',
            status TEXT NOT NULL DEFAULT 'draft',
            priority INTEGER NOT NULL DEFAULT 0,
            valid_from TIMESTAMP,
            valid_until TIMESTAMP,
            current_version_id INTEGER,
            approved_by TEXT,
            approved_at TIMESTAMP,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "knowledge_document_versions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            author TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "knowledge_chunks",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            document_version_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            search_text TEXT NOT NULL,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            embedding_model TEXT,
            embedding_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )


def _create_knowledge_indexes(cursor: Any) -> None:
    create_unique_index(
        cursor,
        "idx_knowledge_documents_company_ref",
        "knowledge_documents",
        "company_id, external_ref",
    )
    create_index(
        cursor,
        "idx_knowledge_documents_company_status",
        "knowledge_documents",
        "company_id, status, document_type",
    )
    create_unique_index(
        cursor,
        "idx_knowledge_document_versions_doc_version",
        "knowledge_document_versions",
        "document_id, version",
    )
    create_index(
        cursor,
        "idx_knowledge_chunks_company_document",
        "knowledge_chunks",
        "company_id, document_id, chunk_index",
    )
    create_index(
        cursor,
        "idx_knowledge_chunks_company_version",
        "knowledge_chunks",
        "company_id, document_version_id",
    )


def _create_conversation_import_tables(cursor: Any) -> None:
    create_table(
        cursor,
        "conversation_import_batches",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            batch_key TEXT NOT NULL,
            source_format TEXT NOT NULL,
            source_label TEXT,
            status TEXT NOT NULL DEFAULT 'received',
            dry_run INTEGER NOT NULL DEFAULT 1,
            stats_json TEXT NOT NULL DEFAULT '{}',
            error_detail TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "imported_conversations",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            external_conversation_id TEXT NOT NULL,
            contact_ref_hash TEXT NOT NULL,
            session_index INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            message_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'staged',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "imported_messages",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            anonymized_content TEXT NOT NULL,
            pii_report_json TEXT NOT NULL DEFAULT '{}',
            occurred_at TIMESTAMP,
            source_line INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "training_candidates",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            batch_id INTEGER,
            conversation_id INTEGER,
            candidate_hash TEXT NOT NULL,
            group_key TEXT NOT NULL,
            scenario_type TEXT NOT NULL DEFAULT 'general',
            customer_profile TEXT,
            profile_name TEXT,
            context_json TEXT NOT NULL DEFAULT '[]',
            customer_message TEXT NOT NULL,
            approved_reply TEXT NOT NULL,
            quality_score REAL NOT NULL DEFAULT 0,
            pii_report_json TEXT NOT NULL DEFAULT '{}',
            review_status TEXT NOT NULL DEFAULT 'pending',
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            promoted_message_example_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )


def _create_conversation_import_indexes(cursor: Any) -> None:
    create_unique_index(
        cursor,
        "idx_import_batches_company_key",
        "conversation_import_batches",
        "company_id, batch_key",
    )
    create_index(
        cursor,
        "idx_import_batches_company_status",
        "conversation_import_batches",
        "company_id, status, created_at",
    )
    create_unique_index(
        cursor,
        "idx_imported_conversations_batch_external",
        "imported_conversations",
        "company_id, batch_id, external_conversation_id",
    )
    create_unique_index(
        cursor,
        "idx_imported_messages_conversation_sequence",
        "imported_messages",
        "company_id, conversation_id, sequence",
    )
    create_unique_index(
        cursor,
        "idx_training_candidates_company_hash",
        "training_candidates",
        "company_id, candidate_hash",
    )
    create_index(
        cursor,
        "idx_training_candidates_company_review",
        "training_candidates",
        "company_id, review_status, created_at",
    )


def _create_dataset_tables(cursor: Any) -> None:
    create_table(
        cursor,
        "dataset_versions",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            content_hash TEXT,
            split_seed INTEGER NOT NULL DEFAULT 42,
            validation_ratio REAL NOT NULL DEFAULT 0.2,
            holdout_ratio REAL NOT NULL DEFAULT 0.1,
            stats_json TEXT NOT NULL DEFAULT '{}',
            readiness_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            frozen_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            frozen_at TIMESTAMP,
            superseded_at TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "dataset_version_items",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            dataset_version_id INTEGER NOT NULL,
            split TEXT NOT NULL,
            group_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER,
            scenario_type TEXT NOT NULL DEFAULT 'general',
            customer_profile TEXT,
            profile_name TEXT,
            context_json TEXT NOT NULL DEFAULT '[]',
            customer_message TEXT NOT NULL,
            approved_reply TEXT NOT NULL,
            style_notes TEXT,
            quality_score REAL NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL,
            near_duplicate_key TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )


def _create_dataset_indexes(cursor: Any) -> None:
    create_unique_index(
        cursor,
        "idx_dataset_versions_company_version",
        "dataset_versions",
        "company_id, version",
    )
    create_index(
        cursor,
        "idx_dataset_versions_company_status",
        "dataset_versions",
        "company_id, status, created_at",
    )
    create_index(
        cursor,
        "idx_dataset_items_version_split",
        "dataset_version_items",
        "dataset_version_id, split",
    )
    create_index(
        cursor,
        "idx_dataset_items_company_group",
        "dataset_version_items",
        "company_id, dataset_version_id, group_key",
    )


def _create_evaluation_tables(cursor: Any) -> None:
    create_table(
        cursor,
        "evaluation_cases",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            case_key TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            scenario_type TEXT NOT NULL DEFAULT 'general',
            customer_profile TEXT,
            customer_message TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '[]',
            expected_behavior TEXT,
            expected_keywords_json TEXT NOT NULL DEFAULT '[]',
            forbidden_keywords_json TEXT NOT NULL DEFAULT '[]',
            requires_handoff INTEGER NOT NULL DEFAULT 0,
            requires_source_grounding INTEGER NOT NULL DEFAULT 0,
            requires_action_tool INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "evaluation_runs",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            run_key TEXT NOT NULL,
            approach TEXT NOT NULL,
            model_ref TEXT,
            dataset_version_id INTEGER,
            status TEXT NOT NULL DEFAULT 'completed',
            total_cases INTEGER NOT NULL DEFAULT 0,
            passed_cases INTEGER NOT NULL DEFAULT 0,
            scores_json TEXT NOT NULL DEFAULT '{}',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_table(
        cursor,
        "evaluation_results",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            case_id INTEGER NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            dimension_scores_json TEXT NOT NULL DEFAULT '{}',
            failure_reasons_json TEXT NOT NULL DEFAULT '[]',
            response_excerpt TEXT,
            sources_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )


def _create_evaluation_indexes(cursor: Any) -> None:
    create_unique_index(
        cursor,
        "idx_evaluation_cases_company_key_version",
        "evaluation_cases",
        "company_id, case_key, version",
    )
    create_index(
        cursor,
        "idx_evaluation_cases_company_active",
        "evaluation_cases",
        "company_id, active, scenario_type",
    )
    create_unique_index(
        cursor,
        "idx_evaluation_runs_company_key",
        "evaluation_runs",
        "company_id, run_key",
    )
    create_index(
        cursor,
        "idx_evaluation_runs_company_approach",
        "evaluation_runs",
        "company_id, approach, created_at",
    )
    create_index(
        cursor,
        "idx_evaluation_results_run",
        "evaluation_results",
        "company_id, run_id",
    )


def _create_company_models(cursor: Any) -> None:
    create_table(
        cursor,
        "company_models",
        """
            id ID_PK,
            company_id INTEGER NOT NULL,
            model_ref TEXT NOT NULL,
            base_model TEXT,
            dataset_version_id INTEGER,
            finetuning_job_id TEXT,
            evaluation_run_id INTEGER,
            status TEXT NOT NULL DEFAULT 'candidate',
            notes TEXT,
            activated_by TEXT,
            activated_at TIMESTAMP,
            retired_at TIMESTAMP,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """,
    )
    create_unique_index(
        cursor,
        "idx_company_models_company_ref",
        "company_models",
        "company_id, model_ref",
    )
    create_index(
        cursor,
        "idx_company_models_company_status",
        "company_models",
        "company_id, status, created_at",
    )


def _add_message_example_governance_columns(cursor: Any) -> None:
    ensure_column(cursor, "message_examples", "customer_profile", "TEXT")
    ensure_column(cursor, "message_examples", "approval_status", "TEXT NOT NULL DEFAULT 'approved'")
    ensure_column(cursor, "message_examples", "source_type", "TEXT NOT NULL DEFAULT 'manual'")
    ensure_column(cursor, "message_examples", "source_reference", "TEXT")
    ensure_column(cursor, "message_examples", "pii_scrubbed", "INTEGER NOT NULL DEFAULT 0")
    create_index(
        cursor,
        "idx_message_examples_company_profile_scenario",
        "message_examples",
        "company_id, customer_profile, scenario_type, active",
    )


def _add_finetuning_job_dataset_columns(cursor: Any) -> None:
    ensure_column(cursor, "finetuning_jobs", "dataset_version_id", "INTEGER")
    ensure_column(cursor, "finetuning_jobs", "readiness_json", "TEXT")
    ensure_column(cursor, "finetuning_jobs", "requested_by", "TEXT")
    create_index(
        cursor,
        "idx_finetuning_jobs_dataset_version",
        "finetuning_jobs",
        "company_id, dataset_version_id",
    )


def _add_message_retrieval_columns(cursor: Any) -> None:
    ensure_column(cursor, "messages", "retrieval_json", "TEXT")


PLATFORM_MIGRATIONS: list[Migration] = [
    (
        "038_company_channel_bindings",
        "Create verifiable provider channel bindings per company",
        _create_company_channel_bindings,
    ),
    (
        "039_company_context_versions",
        "Create versioned company context storage",
        _create_company_context_versions,
    ),
    ("040_knowledge_tables", "Create knowledge documents, versions and chunks", _create_knowledge_tables),
    ("041_knowledge_indexes", "Create knowledge retrieval indexes", _create_knowledge_indexes),
    (
        "042_conversation_import_tables",
        "Create conversation import staging and training candidates",
        _create_conversation_import_tables,
    ),
    (
        "043_conversation_import_indexes",
        "Create conversation import indexes",
        _create_conversation_import_indexes,
    ),
    ("044_dataset_tables", "Create dataset versions and items", _create_dataset_tables),
    ("045_dataset_indexes", "Create dataset indexes", _create_dataset_indexes),
    ("046_evaluation_tables", "Create evaluation cases, runs and results", _create_evaluation_tables),
    ("047_evaluation_indexes", "Create evaluation indexes", _create_evaluation_indexes),
    ("048_company_models", "Create per-company model registry", _create_company_models),
    (
        "049_message_example_governance",
        "Add taxonomy and governance columns to message examples",
        _add_message_example_governance_columns,
    ),
    (
        "050_finetuning_job_dataset_link",
        "Link fine-tuning jobs to frozen dataset versions",
        _add_finetuning_job_dataset_columns,
    ),
    (
        "051_message_retrieval_trace",
        "Store retrieval source trace on generated messages",
        _add_message_retrieval_columns,
    ),
]
