import sqlite3
from pathlib import Path

DB_PATH = Path("project_wapp.db")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {row["name"] for row in cursor.fetchall()}

    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            contact_id TEXT PRIMARY KEY,
            contact_name TEXT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL,
            message TEXT NOT NULL,
            classification TEXT NOT NULL,
            reply TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL,
            customer_message TEXT NOT NULL,
            question_type TEXT NOT NULL,
            question_for_owner TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            owner_answer TEXT,
            resolution_type TEXT,
            customer_followup_question TEXT,
            clarification_consumed INTEGER NOT NULL DEFAULT 0,
            applies_globally INTEGER DEFAULT 0,
            knowledge_action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_question_id INTEGER,
            contact_id TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            knowledge_type TEXT NOT NULL,
            applies_globally INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_question_id) REFERENCES pending_questions(id),
            FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS message_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name TEXT NOT NULL,
            scenario_type TEXT NOT NULL,
            customer_message TEXT NOT NULL,
            approved_reply TEXT NOT NULL,
            style_notes TEXT,
            quality_score REAL DEFAULT 0,
            used_for_training INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL,
            pending_question_id INTEGER,
            message TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            FOREIGN KEY (contact_id) REFERENCES contacts(contact_id),
            FOREIGN KEY (pending_question_id) REFERENCES pending_questions(id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS finetuning_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            base_model TEXT,
            suffix TEXT,
            training_file_id TEXT,
            validation_file_id TEXT,
            fine_tuned_model TEXT,
            provider_error_code TEXT,
            provider_error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            business_type TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    _ensure_column(cursor, "companies", "slug", "TEXT NOT NULL")
    _ensure_column(cursor, "companies", "name", "TEXT NOT NULL")
    _ensure_column(cursor, "companies", "business_type", "TEXT")
    _ensure_column(cursor, "companies", "active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cursor, "companies", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "companies", "updated_at", "TIMESTAMP")
    _ensure_column(cursor, "contacts", "last_classification", "TEXT")
    _ensure_column(cursor, "contacts", "last_interaction_at", "TIMESTAMP")
    _ensure_column(cursor, "contacts", "conversation_summary", "TEXT")
    _ensure_column(cursor, "contacts", "pipeline_stage", "TEXT")
    _ensure_column(cursor, "contacts", "next_action", "TEXT")
    _ensure_column(cursor, "contacts", "lead_temperature", "TEXT")
    _ensure_column(cursor, "contacts", "updated_at", "TIMESTAMP")

    _ensure_column(cursor, "contacts", "company_id", "INTEGER")
    _ensure_column(cursor, "messages", "classification_reason", "TEXT")
    _ensure_column(cursor, "messages", "classification_confidence", "REAL")
    _ensure_column(cursor, "messages", "auto_reply", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "messages", "reply_source", "TEXT")
    _ensure_column(cursor, "messages", "action", "TEXT")
    _ensure_column(cursor, "messages", "priority", "TEXT")
    _ensure_column(cursor, "messages", "company_id", "INTEGER")
    _ensure_column(cursor, "pending_questions", "contact_id", "TEXT NOT NULL")
    _ensure_column(cursor, "pending_questions", "customer_message", "TEXT NOT NULL")
    _ensure_column(cursor, "pending_questions", "question_type", "TEXT NOT NULL")
    _ensure_column(cursor, "pending_questions", "question_for_owner", "TEXT NOT NULL")
    _ensure_column(cursor, "pending_questions", "status", "TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(cursor, "pending_questions", "owner_answer", "TEXT")
    _ensure_column(cursor, "pending_questions", "resolution_type", "TEXT")
    _ensure_column(cursor, "pending_questions", "customer_followup_question", "TEXT")
    _ensure_column(cursor, "pending_questions", "clarification_consumed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "pending_questions", "applies_globally", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "pending_questions", "knowledge_action", "TEXT")
    _ensure_column(cursor, "pending_questions", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "pending_questions", "resolved_at", "TIMESTAMP")
    _ensure_column(cursor, "pending_questions", "company_id", "INTEGER")
    _ensure_column(cursor, "knowledge_entries", "source_question_id", "INTEGER")
    _ensure_column(cursor, "knowledge_entries", "contact_id", "TEXT")
    _ensure_column(cursor, "knowledge_entries", "title", "TEXT NOT NULL")
    _ensure_column(cursor, "knowledge_entries", "content", "TEXT NOT NULL")
    _ensure_column(cursor, "knowledge_entries", "knowledge_type", "TEXT NOT NULL")
    _ensure_column(cursor, "knowledge_entries", "applies_globally", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "knowledge_entries", "status", "TEXT NOT NULL DEFAULT 'active'")
    _ensure_column(cursor, "knowledge_entries", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "knowledge_entries", "company_id", "INTEGER")
    _ensure_column(cursor, "message_examples", "profile_name", "TEXT NOT NULL")
    _ensure_column(cursor, "message_examples", "scenario_type", "TEXT NOT NULL")
    _ensure_column(cursor, "message_examples", "customer_message", "TEXT NOT NULL")
    _ensure_column(cursor, "message_examples", "approved_reply", "TEXT NOT NULL")
    _ensure_column(cursor, "message_examples", "style_notes", "TEXT")
    _ensure_column(cursor, "message_examples", "quality_score", "REAL DEFAULT 0")
    _ensure_column(cursor, "message_examples", "used_for_training", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor, "message_examples", "active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(cursor, "message_examples", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "message_examples", "company_id", "INTEGER")
    _ensure_column(cursor, "outbound_messages", "contact_id", "TEXT NOT NULL")
    _ensure_column(cursor, "outbound_messages", "pending_question_id", "INTEGER")
    _ensure_column(cursor, "outbound_messages", "message", "TEXT NOT NULL")
    _ensure_column(cursor, "outbound_messages", "source", "TEXT NOT NULL")
    _ensure_column(cursor, "outbound_messages", "status", "TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(cursor, "outbound_messages", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "outbound_messages", "sent_at", "TIMESTAMP")
    _ensure_column(cursor, "outbound_messages", "company_id", "INTEGER")
    _ensure_column(cursor, "finetuning_jobs", "company_id", "INTEGER")
    _ensure_column(cursor, "finetuning_jobs", "job_id", "TEXT NOT NULL")
    _ensure_column(cursor, "finetuning_jobs", "status", "TEXT NOT NULL")
    _ensure_column(cursor, "finetuning_jobs", "base_model", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "suffix", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "training_file_id", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "validation_file_id", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "fine_tuned_model", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "provider_error_code", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "provider_error_message", "TEXT")
    _ensure_column(cursor, "finetuning_jobs", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column(cursor, "finetuning_jobs", "finished_at", "TIMESTAMP")

    connection.commit()
    connection.close()