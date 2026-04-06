import sqlite3
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from app.core.config import settings

DB_PATH = Path("project_wapp.db")
SQLITE_ID_TABLES = {
    "messages",
    "pending_questions",
    "knowledge_entries",
    "message_examples",
    "outbound_messages",
    "finetuning_jobs",
    "companies",
    "audit_events",
    "calendar_integrations",
    "appointment_bookings",
    "conversation_flows",
    "customer_reminders",
}
Migration = tuple[str, str, Callable[[Any], None]]
_POSTGRES_POOL = None


def get_database_backend() -> str:
    normalized_url = (settings.database_url or "").strip()
    parsed = urlparse(normalized_url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return "postgres"
    return "sqlite"


def _resolve_sqlite_path(database_url: str | None = None) -> Path:
    normalized_url = (database_url or settings.database_url or "").strip()
    if not normalized_url:
        return DB_PATH

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"", "sqlite"}:
        raise ValueError("DATABASE_URL must use sqlite:// or postgresql://.")

    if not parsed.scheme:
        return Path(normalized_url)

    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raise ValueError("SQLite DATABASE_URL must not define a remote host.")

    sqlite_path = parsed.path or ""
    if normalized_url.startswith("sqlite:///"):
        sqlite_path = normalized_url.removeprefix("sqlite:///")
    elif normalized_url.startswith("sqlite://"):
        sqlite_path = normalized_url.removeprefix("sqlite://")

    if not sqlite_path:
        return DB_PATH

    if sqlite_path == ":memory:":
        return Path(sqlite_path)

    return Path(sqlite_path)


def get_runtime_database_path() -> Path:
    return _resolve_sqlite_path()


def _redact_database_url(database_url: str | None) -> str | None:
    normalized_url = (database_url or "").strip()
    if not normalized_url:
        return None

    parsed = urlparse(normalized_url)
    if not parsed.scheme or parsed.scheme == "sqlite":
        return normalized_url

    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    if parsed.username:
        return f"{parsed.scheme}://{parsed.username}:***@{host}{port}{path}"
    return f"{parsed.scheme}://{host}{port}{path}"


def _normalize_sequence_params(params: Sequence[Any] | None) -> tuple[Any, ...]:
    if params is None:
        return ()
    if isinstance(params, tuple):
        return params
    return tuple(params)


def _normalize_postgres_query(
    query: str,
    params: Sequence[Any] | Mapping[str, Any] | None,
) -> tuple[str, Sequence[Any] | Mapping[str, Any] | None, str | None]:
    normalized_query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    normalized_query = normalized_query.replace(
        "VALUES (?, ?, ?)",
        "VALUES (%s, %s, %s) ON CONFLICT (contact_id) DO NOTHING",
    ) if "INSERT OR IGNORE INTO contacts" in query else normalized_query
    normalized_query = normalized_query.replace(
        "datetime('now', ?)",
        "(CURRENT_TIMESTAMP + CAST(%s AS interval))",
    )
    normalized_query = normalized_query.replace("?", "%s")

    stripped = normalized_query.strip()
    upper = stripped.upper()
    returning_table = None
    if upper.startswith("INSERT INTO ") and " RETURNING " not in upper:
        table_name = stripped.split()[2].strip('"')
        if table_name in SQLITE_ID_TABLES:
            normalized_query = f"{normalized_query.rstrip()} RETURNING id"
            returning_table = table_name

    if isinstance(params, Mapping):
        return normalized_query, params, returning_table

    return normalized_query, _normalize_sequence_params(params), returning_table


class PostgresCursorWrapper:
    def __init__(self, raw_cursor: Any) -> None:
        self._cursor = raw_cursor
        self.lastrowid: int | None = None

    def execute(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> Any:
        normalized_query, normalized_params, returning_table = _normalize_postgres_query(query, params)
        self._cursor.execute(normalized_query, normalized_params)
        self.lastrowid = None

        if returning_table:
            returned = self._cursor.fetchone()
            if returned:
                self.lastrowid = int(returned["id"])

        return self

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)


class PostgresConnectionWrapper:
    def __init__(self, raw_connection: Any, pool: Any) -> None:
        self._connection = raw_connection
        self._pool = pool

    def cursor(self) -> PostgresCursorWrapper:
        return PostgresCursorWrapper(self._connection.cursor())

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        try:
            self._connection.rollback()
        except Exception:
            pass
        self._pool.putconn(self._connection)


def _get_postgres_connect_kwargs() -> dict[str, Any]:
    return {
        "conninfo": settings.database_url,
        "kwargs": {
            "connect_timeout": 5,
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        },
        "min_size": 1,
        "max_size": max(1, settings.database_pool_size),
    }


def _get_postgres_pool() -> Any:
    global _POSTGRES_POOL
    if _POSTGRES_POOL is not None:
        return _POSTGRES_POOL

    try:
        from psycopg_pool import ConnectionPool  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Postgres backend requires psycopg_pool. Install dependencies from requirements.txt."
        ) from exc

    _POSTGRES_POOL = ConnectionPool(**_get_postgres_connect_kwargs())
    return _POSTGRES_POOL


def get_connection() -> Any:
    backend = get_database_backend()
    if backend == "sqlite":
        connection = sqlite3.connect(get_runtime_database_path())
        connection.row_factory = sqlite3.Row
        return connection

    pool = _get_postgres_pool()
    raw_connection = pool.getconn()
    try:
        from psycopg.rows import dict_row  # type: ignore
    except ImportError as exc:
        pool.putconn(raw_connection)
        raise RuntimeError("Postgres backend requires psycopg with dict_row support.") from exc

    raw_connection.row_factory = dict_row
    return PostgresConnectionWrapper(raw_connection, pool)


def get_runtime_database_summary() -> dict[str, str | bool | int | None]:
    backend = get_database_backend()
    if backend == "sqlite":
        database_path = get_runtime_database_path()
        resolved_path = str(database_path.resolve()) if str(database_path) != ":memory:" else ":memory:"
        return {
            "backend": "sqlite",
            "support_level": "active",
            "configured_url": _redact_database_url(settings.database_url),
            "database_path": resolved_path,
            "file_exists": database_path.exists() if str(database_path) != ":memory:" else True,
            "pool_size": settings.database_pool_size,
            "statement_timeout_ms": settings.database_statement_timeout_ms,
        }

    parsed = urlparse(settings.database_url)
    query_params = parse_qs(parsed.query or "")
    return {
        "backend": "postgres",
        "support_level": "active",
        "configured_url": _redact_database_url(settings.database_url),
        "database_path": None,
        "file_exists": None,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database_name": parsed.path.lstrip("/") or None,
        "sslmode": query_params.get("sslmode", [None])[0],
        "pool_size": settings.database_pool_size,
        "statement_timeout_ms": settings.database_statement_timeout_ms,
    }


def get_postgres_target_summary() -> dict[str, str | bool | int | None]:
    configured_url = (settings.postgres_database_url or "").strip()
    if not configured_url and get_database_backend() == "postgres":
        configured_url = settings.database_url

    if not configured_url:
        return {
            "configured": False,
            "backend": "postgres",
            "support_level": "target_only",
            "configured_url": None,
            "host": None,
            "port": None,
            "database_name": None,
            "sslmode": None,
            "driver_available": False,
        }

    parsed = urlparse(configured_url)
    query_params = parse_qs(parsed.query or "")

    driver_available = False
    try:
        import psycopg  # type: ignore # noqa: F401
        import psycopg_pool  # type: ignore # noqa: F401
    except ImportError:
        driver_available = False
    else:
        driver_available = True

    support_level = "active" if configured_url == settings.database_url and get_database_backend() == "postgres" else "target_only"
    return {
        "configured": True,
        "backend": "postgres",
        "support_level": support_level,
        "configured_url": _redact_database_url(configured_url),
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database_name": parsed.path.lstrip("/") or None,
        "sslmode": query_params.get("sslmode", [None])[0],
        "driver_available": driver_available,
    }


def check_runtime_database_connection() -> dict[str, str | bool]:
    backend = get_database_backend()
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT 1 AS ok")
        cursor.fetchone()
        connection.close()
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"Runtime {backend} database check failed: {exc}",
        }

    return {
        "ok": True,
        "detail": f"Runtime {backend} database accepted a SELECT 1 probe.",
    }


def check_postgres_target_connection() -> dict[str, str | bool]:
    target_summary = get_postgres_target_summary()
    if not target_summary["configured"]:
        return {
            "ok": False,
            "detail": "POSTGRES_DATABASE_URL is not configured.",
        }

    if not target_summary["driver_available"]:
        return {
            "ok": False,
            "detail": "psycopg and psycopg_pool are not installed in the current environment.",
        }

    target_url = settings.postgres_database_url or settings.database_url

    try:
        import psycopg  # type: ignore

        with psycopg.connect(
            target_url,
            connect_timeout=3,
            options=f"-c statement_timeout={settings.database_statement_timeout_ms}",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"Postgres target probe failed: {exc}",
        }

    return {
        "ok": True,
        "detail": "Postgres target accepted a SELECT 1 probe.",
    }


def _ensure_sqlite_column(cursor: Any, table: str, column: str, definition: str) -> None:
    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_postgres_column(cursor: Any, table: str, column: str, definition: str) -> None:
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")


def _create_runtime_tables_sqlite(cursor: Any) -> None:
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

    ensure_column = _ensure_sqlite_column
    _ensure_runtime_columns(ensure_column, cursor)


def _create_runtime_tables_postgres(cursor: Any) -> None:
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
            id BIGSERIAL PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
            message TEXT NOT NULL,
            classification TEXT NOT NULL,
            reply TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_questions (
            id BIGSERIAL PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
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
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id BIGSERIAL PRIMARY KEY,
            source_question_id BIGINT REFERENCES pending_questions(id),
            contact_id TEXT REFERENCES contacts(contact_id),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            knowledge_type TEXT NOT NULL,
            applies_globally INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS message_examples (
            id BIGSERIAL PRIMARY KEY,
            profile_name TEXT NOT NULL,
            scenario_type TEXT NOT NULL,
            customer_message TEXT NOT NULL,
            approved_reply TEXT NOT NULL,
            style_notes TEXT,
            quality_score DOUBLE PRECISION DEFAULT 0,
            used_for_training INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_messages (
            id BIGSERIAL PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
            pending_question_id BIGINT REFERENCES pending_questions(id),
            message TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS finetuning_jobs (
            id BIGSERIAL PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            base_model TEXT,
            suffix TEXT,
            training_file_id TEXT,
            validation_file_id TEXT,
            fine_tuned_model TEXT,
            provider_error_code TEXT,
            provider_error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id BIGSERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            business_type TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ
        )
        """
    )

    ensure_column = _ensure_postgres_column
    _ensure_runtime_columns(ensure_column, cursor)


def _ensure_runtime_columns(ensure_column: Callable[[Any, str, str, str], None], cursor: Any) -> None:
    ensure_column(cursor, "companies", "slug", "TEXT")
    ensure_column(cursor, "companies", "name", "TEXT")
    ensure_column(cursor, "companies", "business_type", "TEXT")
    ensure_column(cursor, "companies", "active", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "companies", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "companies", "updated_at", "TIMESTAMP")
    ensure_column(cursor, "contacts", "last_classification", "TEXT")
    ensure_column(cursor, "contacts", "last_interaction_at", "TIMESTAMP")
    ensure_column(cursor, "contacts", "conversation_summary", "TEXT")
    ensure_column(cursor, "contacts", "pipeline_stage", "TEXT")
    ensure_column(cursor, "contacts", "next_action", "TEXT")
    ensure_column(cursor, "contacts", "lead_temperature", "TEXT")
    ensure_column(cursor, "contacts", "updated_at", "TIMESTAMP")
    ensure_column(cursor, "contacts", "company_id", "INTEGER")
    ensure_column(cursor, "messages", "classification_reason", "TEXT")
    ensure_column(cursor, "messages", "classification_confidence", "REAL")
    ensure_column(cursor, "messages", "auto_reply", "INTEGER DEFAULT 0")
    ensure_column(cursor, "messages", "reply_source", "TEXT")
    ensure_column(cursor, "messages", "action", "TEXT")
    ensure_column(cursor, "messages", "priority", "TEXT")
    ensure_column(cursor, "messages", "company_id", "INTEGER")
    ensure_column(cursor, "pending_questions", "company_id", "INTEGER")
    ensure_column(cursor, "knowledge_entries", "company_id", "INTEGER")
    ensure_column(cursor, "message_examples", "company_id", "INTEGER")
    ensure_column(cursor, "outbound_messages", "company_id", "INTEGER")
    ensure_column(cursor, "finetuning_jobs", "company_id", "INTEGER")


def _create_runtime_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        _create_runtime_tables_postgres(cursor)
        return
    _create_runtime_tables_sqlite(cursor)


def _create_audit_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id BIGSERIAL PRIMARY KEY,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                company_id INTEGER,
                status TEXT NOT NULL,
                request_id TEXT,
                metadata_json TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                company_id INTEGER,
                status TEXT NOT NULL,
                request_id TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "audit_events", "actor_id", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "action", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "resource_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "resource_id", "TEXT")
    ensure_column(cursor, "audit_events", "company_id", "INTEGER")
    ensure_column(cursor, "audit_events", "status", "TEXT NOT NULL DEFAULT 'success'")
    ensure_column(cursor, "audit_events", "request_id", "TEXT")
    ensure_column(cursor, "audit_events", "metadata_json", "TEXT")
    ensure_column(cursor, "audit_events", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")


def _create_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_classification_created_at
        ON messages (classification, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_company_created_at
        ON messages (company_id, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pending_questions_status_created_at
        ON pending_questions (status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbound_messages_status_created_at
        ON outbound_messages (status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
        ON audit_events (created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_action_created_at
        ON audit_events (action, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_resource_created_at
        ON audit_events (resource_type, created_at)
        """
    )


def _create_scale_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contacts_company_updated_at
        ON contacts (company_id, updated_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pending_questions_company_status_created_at
        ON pending_questions (company_id, status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbound_messages_company_status_created_at
        ON outbound_messages (company_id, status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_entries_company_status_created_at
        ON knowledge_entries (company_id, status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_examples_company_active_score
        ON message_examples (company_id, active, quality_score)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_finetuning_jobs_company_created_at
        ON finetuning_jobs (company_id, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_company_created_at
        ON audit_events (company_id, created_at)
        """
    )


def _create_calendar_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_integrations (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                provider TEXT NOT NULL,
                external_calendar_id TEXT,
                config_json TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_bookings (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                contact_id TEXT,
                integration_id BIGINT REFERENCES calendar_integrations(id),
                provider TEXT NOT NULL,
                external_event_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_integrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                provider TEXT NOT NULL,
                external_calendar_id TEXT,
                config_json TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                contact_id TEXT,
                integration_id INTEGER,
                provider TEXT NOT NULL,
                external_event_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                starts_at TIMESTAMP NOT NULL,
                ends_at TIMESTAMP NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (integration_id) REFERENCES calendar_integrations(id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "calendar_integrations", "company_id", "INTEGER")
    ensure_column(cursor, "calendar_integrations", "provider", "TEXT NOT NULL DEFAULT 'mock'")
    ensure_column(cursor, "calendar_integrations", "external_calendar_id", "TEXT")
    ensure_column(cursor, "calendar_integrations", "config_json", "TEXT")
    ensure_column(cursor, "calendar_integrations", "active", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "calendar_integrations", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "calendar_integrations", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    ensure_column(cursor, "appointment_bookings", "company_id", "INTEGER")
    ensure_column(cursor, "appointment_bookings", "contact_id", "TEXT")
    ensure_column(cursor, "appointment_bookings", "integration_id", "INTEGER")
    ensure_column(cursor, "appointment_bookings", "provider", "TEXT NOT NULL DEFAULT 'mock'")
    ensure_column(cursor, "appointment_bookings", "external_event_id", "TEXT")
    ensure_column(cursor, "appointment_bookings", "title", "TEXT NOT NULL DEFAULT 'Agendamento'")
    ensure_column(cursor, "appointment_bookings", "description", "TEXT")
    ensure_column(cursor, "appointment_bookings", "starts_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_bookings", "ends_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_bookings", "timezone", "TEXT NOT NULL DEFAULT 'UTC'")
    ensure_column(cursor, "appointment_bookings", "status", "TEXT NOT NULL DEFAULT 'confirmed'")
    ensure_column(cursor, "appointment_bookings", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "appointment_bookings", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")


def _create_calendar_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calendar_integrations_company_active
        ON calendar_integrations (company_id, active)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_appointment_bookings_company_starts_at
        ON appointment_bookings (company_id, starts_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_appointment_bookings_status_starts_at
        ON appointment_bookings (status, starts_at)
        """
    )


def _create_workflow_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_flows (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                flow_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                state_json TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_reminders (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                reminder_text TEXT NOT NULL,
                reminder_summary TEXT,
                remind_at TIMESTAMPTZ NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                outbound_message_id BIGINT REFERENCES outbound_messages(id),
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                flow_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                state_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                reminder_text TEXT NOT NULL,
                reminder_summary TEXT,
                remind_at TIMESTAMP NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                outbound_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (outbound_message_id) REFERENCES outbound_messages(id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "conversation_flows", "company_id", "INTEGER")
    ensure_column(cursor, "conversation_flows", "contact_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cursor, "conversation_flows", "flow_type", "TEXT NOT NULL DEFAULT 'generic'")
    ensure_column(cursor, "conversation_flows", "status", "TEXT NOT NULL DEFAULT 'active'")
    ensure_column(cursor, "conversation_flows", "state_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_column(cursor, "conversation_flows", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "conversation_flows", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    ensure_column(cursor, "customer_reminders", "company_id", "INTEGER")
    ensure_column(cursor, "customer_reminders", "contact_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cursor, "customer_reminders", "reminder_text", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cursor, "customer_reminders", "reminder_summary", "TEXT")
    ensure_column(cursor, "customer_reminders", "remind_at", "TIMESTAMP")
    ensure_column(cursor, "customer_reminders", "timezone", "TEXT NOT NULL DEFAULT 'UTC'")
    ensure_column(cursor, "customer_reminders", "status", "TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(cursor, "customer_reminders", "outbound_message_id", "INTEGER")
    ensure_column(cursor, "customer_reminders", "audience", "TEXT NOT NULL DEFAULT 'customer'")
    ensure_column(cursor, "customer_reminders", "source", "TEXT NOT NULL DEFAULT 'manual'")
    ensure_column(cursor, "customer_reminders", "appointment_booking_id", "INTEGER")
    ensure_column(cursor, "customer_reminders", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "customer_reminders", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")


def _create_workflow_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversation_flows_contact_status
        ON conversation_flows (contact_id, status, updated_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customer_reminders_status_remind_at
        ON customer_reminders (status, remind_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customer_reminders_company_status
        ON customer_reminders (company_id, status, remind_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customer_reminders_booking_audience
        ON customer_reminders (appointment_booking_id, audience, status)
        """
    )


MIGRATIONS: list[Migration] = [
    ("001_runtime_tables", "Create runtime tables and columns", _create_runtime_tables),
    ("002_audit_events", "Create audit events table", _create_audit_tables),
    ("003_operational_indexes", "Create operational indexes", _create_indexes),
    ("004_scale_indexes", "Create scale-oriented indexes", _create_scale_indexes),
    ("005_calendar_tables", "Create calendar integrations and bookings", _create_calendar_tables),
    ("006_calendar_indexes", "Create calendar indexes", _create_calendar_indexes),
    ("007_workflow_tables", "Create conversation flows and reminders", _create_workflow_tables),
    ("008_workflow_indexes", "Create workflow indexes", _create_workflow_indexes),
    ("009_reminder_audience", "Add audience and booking columns to reminders", _create_workflow_tables),
    ("010_reminder_booking_index", "Add reminder booking index", _create_workflow_indexes),
]


def _ensure_schema_migrations_table(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        return

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _get_applied_migration_versions(cursor: Any) -> set[str]:
    cursor.execute("SELECT version FROM schema_migrations")
    rows = cursor.fetchall()
    return {str(row["version"]) for row in rows}


def _record_migration(cursor: Any, version: str, name: str) -> None:
    cursor.execute(
        """
        INSERT INTO schema_migrations (version, name)
        VALUES (?, ?)
        """,
        (version, name),
    )


def _run_migrations(cursor: Any) -> None:
    _ensure_schema_migrations_table(cursor)
    applied_versions = _get_applied_migration_versions(cursor)

    for version, name, migration_fn in MIGRATIONS:
        if version in applied_versions:
            continue
        migration_fn(cursor)
        _record_migration(cursor, version, name)


def init_db() -> None:
    connection = get_connection()
    cursor = connection.cursor()
    _run_migrations(cursor)
    connection.commit()
    connection.close()
