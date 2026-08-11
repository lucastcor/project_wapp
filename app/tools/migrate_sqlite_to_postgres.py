import argparse
import sqlite3
from collections.abc import Sequence
from contextlib import closing

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
            "cancelled_at",
            "cancel_reason",
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
]


def _build_insert_statement(table: str, columns: Sequence[str]) -> str:
    columns_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})"


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
    cursor.execute(f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in cursor.fetchall()}


def _fetch_sqlite_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
) -> list[sqlite3.Row]:
    available_columns = _sqlite_table_columns(connection, table)
    selected_columns = [
        column if column in available_columns else f"NULL AS {column}"
        for column in columns
    ]
    cursor = connection.cursor()
    cursor.execute(f"SELECT {', '.join(selected_columns)} FROM {table}")
    return cursor.fetchall()


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

    import psycopg  # type: ignore

    # D-13: `sqlite3.connect` ficava fora de qualquer `finally`. Uma falha
    # dentro do bloco do psycopg — tabela ausente, erro de conversão — deixava
    # o handle SQLite aberto com transação de leitura. É o padrão do I-01
    # verbatim; aqui o processo é de um disparo só, mas o formato é o mesmo.
    with closing(sqlite3.connect(sqlite_path)) as sqlite_connection, psycopg.connect(
        postgres_url
    ) as postgres_connection:
        sqlite_connection.row_factory = sqlite3.Row
        with postgres_connection.cursor() as postgres_cursor:
            source_has_contact_profiles = _sqlite_table_exists(sqlite_connection, "contact_company_profiles")
            if truncate_first:
                ordered_tables = [table for table, _, _ in reversed(TABLE_COLUMNS)]
                postgres_cursor.execute(f"TRUNCATE TABLE {', '.join(ordered_tables)} RESTART IDENTITY CASCADE")

            for table, columns, identity_column in TABLE_COLUMNS:
                if not _sqlite_table_exists(sqlite_connection, table):
                    continue

                rows = _fetch_sqlite_rows(sqlite_connection, table, columns)
                if not rows:
                    continue

                insert_sql = _build_insert_statement(table, columns)
                values = [tuple(row[column] for column in columns) for row in rows]
                postgres_cursor.executemany(insert_sql, values)

                if identity_column:
                    _reset_identity(postgres_cursor, table, identity_column)

            if not source_has_contact_profiles and _sqlite_table_exists(sqlite_connection, "contacts"):
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

        postgres_connection.commit()


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
