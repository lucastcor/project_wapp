import argparse
import sqlite3
from collections.abc import Sequence

from app.core.config import settings
from app.storage.db import init_db


TABLE_COLUMNS: list[tuple[str, list[str], str | None]] = [
    (
        "companies",
        ["id", "slug", "name", "business_type", "active", "created_at", "updated_at"],
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
            "created_at",
            "sent_at",
        ],
        "id",
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
        ],
        "id",
    ),
    (
        "audit_events",
        [
            "id",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "company_id",
            "status",
            "request_id",
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
    (
        "appointment_bookings",
        [
            "id",
            "company_id",
            "contact_id",
            "integration_id",
            "provider",
            "external_event_id",
            "title",
            "description",
            "starts_at",
            "ends_at",
            "timezone",
            "status",
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
            "created_at",
            "updated_at",
        ],
        "id",
    ),
]


def _build_insert_statement(table: str, columns: Sequence[str]) -> str:
    columns_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})"


def _reset_identity(cursor, table: str, id_column: str) -> None:
    cursor.execute(
        f"""
        SELECT setval(
            pg_get_serial_sequence(%s, %s),
            COALESCE((SELECT MAX({id_column}) FROM {table}), 1),
            COALESCE((SELECT MAX({id_column}) IS NOT NULL FROM {table}), false)
        )
        """,
        (table, id_column),
    )


def migrate(sqlite_path: str, postgres_url: str, truncate_first: bool = True) -> None:
    settings.database_url = postgres_url
    settings.postgres_database_url = postgres_url
    init_db()

    sqlite_connection = sqlite3.connect(sqlite_path)
    sqlite_connection.row_factory = sqlite3.Row

    import psycopg  # type: ignore

    with psycopg.connect(postgres_url) as postgres_connection:
        with postgres_connection.cursor() as postgres_cursor:
            if truncate_first:
                ordered_tables = [table for table, _, _ in reversed(TABLE_COLUMNS)]
                postgres_cursor.execute(f"TRUNCATE TABLE {', '.join(ordered_tables)} RESTART IDENTITY CASCADE")

            for table, columns, identity_column in TABLE_COLUMNS:
                sqlite_cursor = sqlite_connection.cursor()
                sqlite_cursor.execute(f"SELECT {', '.join(columns)} FROM {table}")
                rows = sqlite_cursor.fetchall()
                if not rows:
                    continue

                insert_sql = _build_insert_statement(table, columns)
                values = [tuple(row[column] for column in columns) for row in rows]
                postgres_cursor.executemany(insert_sql, values)

                if identity_column:
                    _reset_identity(postgres_cursor, table, identity_column)

        postgres_connection.commit()

    sqlite_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra dados do SQLite local para Postgres.")
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

    postgres_url = args.postgres_url or settings.postgres_database_url or settings.database_url
    if not postgres_url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("A URL de destino precisa ser Postgres.")

    migrate(
        sqlite_path=args.sqlite_path,
        postgres_url=postgres_url,
        truncate_first=not args.keep_target_data,
    )
    print("Migracao concluida com sucesso.")


if __name__ == "__main__":
    main()
