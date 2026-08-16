import importlib.util
import itertools
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
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
    "appointment_queue_entries",
    "appointment_queue_offers",
    "billing_plans",
    "billing_subscriptions",
    "billing_usage_records",
    "inbound_message_receipts",
    "whatsapp_channel_bindings",
    "saas_users",
    "company_memberships",
    "user_invitations",
    "user_sessions",
    "password_reset_tokens",
    "billing_webhook_events",
}
TENANT_REFERENCE_TABLES = (
    "appointment_bookings",
    "appointment_queue_entries",
    "appointment_queue_offers",
    "audit_events",
    "billing_subscriptions",
    "billing_usage_records",
    "calendar_integrations",
    "company_contexts",
    "company_memberships",
    "contact_company_profiles",
    "contacts",
    "conversation_flows",
    "customer_reminders",
    "finetuning_jobs",
    "inbound_message_receipts",
    "knowledge_entries",
    "message_examples",
    "messages",
    "outbound_messages",
    "pending_questions",
    "user_invitations",
    "whatsapp_channel_bindings",
)
TENANT_REFERENTIAL_LINKS = (
    ("appointment_bookings", "integration_id", "calendar_integrations"),
    ("appointment_queue_entries", "original_booking_id", "appointment_bookings"),
    ("appointment_queue_offers", "queue_entry_id", "appointment_queue_entries"),
    ("appointment_queue_offers", "released_booking_id", "appointment_bookings"),
    ("appointment_queue_offers", "outbound_message_id", "outbound_messages"),
    ("appointment_queue_offers", "new_booking_id", "appointment_bookings"),
    ("billing_usage_records", "subscription_id", "billing_subscriptions"),
    ("customer_reminders", "appointment_booking_id", "appointment_bookings"),
    ("customer_reminders", "outbound_message_id", "outbound_messages"),
    ("knowledge_entries", "source_question_id", "pending_questions"),
    ("outbound_messages", "pending_question_id", "pending_questions"),
)
Migration = tuple[str, str, Callable[[Any], None]]
# Fixed key for the Postgres advisory lock that serializes schema migrations.
MIGRATION_ADVISORY_LOCK_KEY = 7723001122334455
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
    if "INSERT OR IGNORE INTO contacts" in query:
        normalized_query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        normalized_query = normalized_query.replace("?", "%s")
        if " ON CONFLICT " not in normalized_query.upper():
            normalized_query = f"{normalized_query.rstrip()} ON CONFLICT (contact_id) DO NOTHING"
    else:
        normalized_query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO")

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
    """Pooled Postgres connection with an idempotent, single-return release.

    A connection must go back to the pool exactly once, and never while its
    transaction is still open — an aborted transaction returned to the pool
    poisons the next borrower and, repeated, exhausts the pool.
    """

    def __init__(self, raw_connection: Any, pool: Any) -> None:
        self._connection = raw_connection
        self._pool = pool
        self._released = False
        # Set when release could not complete cleanly; useful for diagnostics.
        self.release_error: Exception | None = None

    @property
    def released(self) -> bool:
        return self._released

    def cursor(self) -> PostgresCursorWrapper:
        if self._released:
            raise RuntimeError("Cannot use a connection that was already returned to the pool.")
        return PostgresCursorWrapper(self._connection.cursor())

    def commit(self) -> None:
        if self._released:
            raise RuntimeError("Cannot commit a connection that was already returned to the pool.")
        self._connection.commit()

    def rollback(self) -> None:
        if self._released:
            return
        self._connection.rollback()

    def close(self) -> None:
        """Return the connection to the pool exactly once.

        Ordering matters: ``_released`` is set before any cleanup runs, so a
        failure part-way through can never lead to a second ``putconn`` of the
        same raw connection. Cleanup problems are recorded on ``release_error``
        and never raised, so they cannot mask the application exception that was
        already propagating when the connection was being released.
        """
        if self._released:
            return
        self._released = True

        try:
            self._connection.rollback()
        except Exception as rollback_error:
            self.release_error = rollback_error

        try:
            self._pool.putconn(self._connection)
        except Exception as putconn_error:
            # The pool would not take it back, so the slot is only recovered by
            # closing the raw connection; leaving it open would strand it and
            # eventually starve the pool.
            self.release_error = putconn_error
            self._discard_raw_connection()

    def _discard_raw_connection(self) -> None:
        try:
            self._connection.close()
        except Exception as discard_error:
            self.release_error = discard_error


class SqliteConnectionWrapper:
    """SQLite connection with the same idempotent release contract as Postgres."""

    def __init__(self, raw_connection: Any) -> None:
        self._connection = raw_connection
        self._released = False
        self.release_error: Exception | None = None

    @property
    def released(self) -> bool:
        return self._released

    def cursor(self) -> Any:
        if self._released:
            raise RuntimeError("Cannot use a connection that was already closed.")
        return self._connection.cursor()

    def commit(self) -> None:
        if self._released:
            raise RuntimeError("Cannot commit a connection that was already closed.")
        self._connection.commit()

    def rollback(self) -> None:
        if self._released:
            return
        self._connection.rollback()

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._connection.rollback()
        except Exception as rollback_error:
            self.release_error = rollback_error
        self._connection.close()


def _get_postgres_connect_kwargs() -> dict[str, Any]:
    return {
        "conninfo": settings.database_url,
        "kwargs": {
            "connect_timeout": 5,
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        },
        "min_size": 1,
        "max_size": max(1, settings.database_pool_size),
        # Explicit rather than relying on the implicit-open default, which
        # psycopg_pool deprecates. The pool is only built on first use, so this
        # still does not open a connection at import time.
        "open": True,
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


def close_postgres_pool() -> None:
    global _POSTGRES_POOL
    if _POSTGRES_POOL is None:
        return

    try:
        _POSTGRES_POOL.close()
    finally:
        _POSTGRES_POOL = None


def get_connection() -> Any:
    backend = get_database_backend()
    if backend == "sqlite":
        raw_sqlite = sqlite3.connect(
            get_runtime_database_path(),
            timeout=max(5.0, settings.database_statement_timeout_ms / 1000),
        )
        raw_sqlite.row_factory = sqlite3.Row
        # SQLite does not enforce declared foreign keys unless every connection
        # enables them. Tenant isolation must not depend on the caller.
        raw_sqlite.execute("PRAGMA foreign_keys = ON")
        # PRAGMA takes no bind parameters, so the value is interpolated; int()
        # makes it structurally impossible for the setting to carry SQL.
        busy_timeout_ms = int(max(5000, settings.database_statement_timeout_ms))
        raw_sqlite.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        return _register_connection(SqliteConnectionWrapper(raw_sqlite))

    pool = _get_postgres_pool()
    raw_connection = pool.getconn()
    try:
        from psycopg.rows import dict_row  # type: ignore
    except ImportError as exc:
        pool.putconn(raw_connection)
        raise RuntimeError("Postgres backend requires psycopg with dict_row support.") from exc

    raw_connection.row_factory = dict_row
    return _register_connection(PostgresConnectionWrapper(raw_connection, pool))


# ── Connection lifecycle ──────────────────────────────────────────────
#
# Every acquired connection must be released exactly once, and a failed
# transaction must be rolled back before the connection returns to the pool.
# Two mechanisms cooperate:
#
# 1. ``db_session`` / ``db_transaction`` context managers — the pattern for new
#    code and for standalone tools.
# 2. ``connection_scope`` — a guard installed around every public
#    ``RuntimeRepository`` method, so the legacy methods that release on their
#    success path only are still safe when an operation raises midway.
#
# Neither relies on ``__del__``, garbage collection or interpreter shutdown.

_ACTIVE_CONNECTIONS: ContextVar[list[Any] | None] = ContextVar(
    "project_wapp_active_connections",
    default=None,
)


UNIQUE_VIOLATION_SQLSTATE = "23505"


def is_unique_violation(
    error: BaseException,
    *,
    postgres_constraint: str,
    sqlite_qualified_column: str,
) -> bool:
    """Whether ``error`` is a unique violation on one specific constraint.

    Deliberately narrow: an unrelated integrity error (a foreign key, a NOT NULL,
    or a different unique index) must keep propagating instead of being
    translated into a domain conflict.

    * PostgreSQL exposes ``sqlstate`` 23505 plus the offending constraint name.
    * SQLite only reports a message, so the qualified ``table.column`` is matched.
    """
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate is not None:
        if sqlstate != UNIQUE_VIOLATION_SQLSTATE:
            return False
        diagnostics = getattr(error, "diag", None)
        constraint_name = getattr(diagnostics, "constraint_name", None) if diagnostics else None
        return constraint_name == postgres_constraint

    if isinstance(error, sqlite3.IntegrityError):
        message = str(error)
        return "UNIQUE constraint failed" in message and sqlite_qualified_column in message

    return False


def _log_release_errors(release_errors: list[Exception]) -> None:
    """Surface degraded connection releases without masking the original error."""
    # Imported lazily so the storage layer keeps no import-time dependency on
    # the logging configuration.
    import logging

    from app.core.logging import get_logger, log_event

    log_event(
        get_logger(__name__),
        logging.WARNING,
        "storage.connection.release_failed",
        failed_release_count=len(release_errors),
        error_types=sorted({type(error).__name__ for error in release_errors}),
    )


def _register_connection(connection: Any) -> Any:
    scope = _ACTIVE_CONNECTIONS.get()
    if scope is not None:
        scope.append(connection)
    return connection


@contextmanager
def connection_scope() -> Iterator[None]:
    """Release every connection acquired inside the block, on success or failure.

    Nested scopes are independent: an inner scope only releases the connections
    opened inside it. ``close()`` is idempotent, so connections a method already
    closed on its own are left untouched.
    """
    token = _ACTIVE_CONNECTIONS.set([])
    try:
        yield
    finally:
        scope = _ACTIVE_CONNECTIONS.get() or []
        _ACTIVE_CONNECTIONS.reset(token)
        release_errors: list[Exception] = []
        for connection in scope:
            try:
                connection.close()
            except Exception as release_error:
                # Releasing the remaining connections matters more than one
                # failing release; a broken connection is dropped by the pool.
                release_errors.append(release_error)
        if release_errors:
            _log_release_errors(release_errors)


@contextmanager
def db_session() -> Iterator[Any]:
    """Read-oriented connection scope. Rolls back and releases on exit."""
    connection = get_connection()
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def db_transaction() -> Iterator[Any]:
    """Write connection scope: commits on success, rolls back on failure."""
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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

    # Availability probe only: find_spec avoids importing the drivers (and their
    # C extensions) just to report whether the Postgres backend is installable.
    driver_available = all(
        importlib.util.find_spec(module) is not None
        for module in ("psycopg", "psycopg_pool")
    )

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
        # Readiness runs on every probe; a leaked connection here would drain the
        # pool faster than any other code path.
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"Runtime {backend} database check failed: {exc}",
        }

    return {
        "ok": True,
        "detail": f"Runtime {backend} database accepted a SELECT 1 probe.",
    }


def check_database_schema_current() -> dict[str, Any]:
    expected_versions = {version for version, _name, _migration in MIGRATIONS}
    try:
        with db_session() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT version FROM schema_migrations")
            applied_versions = {str(row["version"]) for row in cursor.fetchall()}
    except Exception as exc:
        return {
            "ok": False,
            "missing_versions": sorted(expected_versions),
            "detail": f"Schema migration ledger is unavailable: {type(exc).__name__}.",
        }
    missing_versions = sorted(expected_versions - applied_versions)
    return {
        "ok": not missing_versions,
        "missing_versions": missing_versions,
        "detail": (
            "Database schema is current."
            if not missing_versions
            else f"Database schema is missing migrations: {', '.join(missing_versions)}."
        ),
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


_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# DDL column definitions are migration-authored fragments; keep them to the
# small vocabulary the migrations actually use so nothing else can slip in.
# Covers the vocabulary the migrations use: types, NOT NULL, DEFAULT literals
# (including JSON defaults such as '{}'), precision and enum-ish check values.
# Notably excludes ';' so a definition cannot append a second statement.
_SQL_COLUMN_DEFINITION_PATTERN = re.compile(r"""^[A-Za-z0-9_ ()'{}:.,\-]+$""")


def _assert_sql_identifier(value: str, *, kind: str) -> str:
    """Reject anything that is not a bare SQL identifier.

    These helpers receive their arguments from migration code only, but the
    check makes that guarantee local and enforceable instead of relying on
    every present and future call site.
    """
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe {kind} identifier in migration: {value!r}")
    return value


def _assert_sql_column_definition(definition: str) -> str:
    if not _SQL_COLUMN_DEFINITION_PATTERN.fullmatch(definition):
        raise ValueError(f"Unsafe column definition in migration: {definition!r}")
    return definition


def _ensure_sqlite_column(cursor: Any, table: str, column: str, definition: str) -> None:
    safe_table = _assert_sql_identifier(table, kind="table")
    safe_column = _assert_sql_identifier(column, kind="column")
    safe_definition = _assert_sql_column_definition(definition)
    cursor.execute(f"PRAGMA table_info({safe_table})")
    existing_columns = {row["name"] for row in cursor.fetchall()}
    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {safe_table} ADD COLUMN {safe_column} {safe_definition}")


def _ensure_postgres_column(cursor: Any, table: str, column: str, definition: str) -> None:
    safe_table = _assert_sql_identifier(table, kind="table")
    safe_column = _assert_sql_identifier(column, kind="column")
    safe_definition = _assert_sql_column_definition(definition)
    cursor.execute(
        f"ALTER TABLE {safe_table} ADD COLUMN IF NOT EXISTS {safe_column} {safe_definition}"
    )


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
    ensure_column(cursor, "contacts", "customer_profile", "TEXT")
    ensure_column(cursor, "contacts", "notes", "TEXT")
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
                admin_role TEXT,
                auth_mode TEXT,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                company_id INTEGER,
                status TEXT NOT NULL,
                request_id TEXT,
                http_method TEXT,
                http_path TEXT,
                client_ip TEXT,
                user_agent TEXT,
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
                admin_role TEXT,
                auth_mode TEXT,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                company_id INTEGER,
                status TEXT NOT NULL,
                request_id TEXT,
                http_method TEXT,
                http_path TEXT,
                client_ip TEXT,
                user_agent TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "audit_events", "actor_id", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "admin_role", "TEXT")
    ensure_column(cursor, "audit_events", "auth_mode", "TEXT")
    ensure_column(cursor, "audit_events", "action", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "resource_type", "TEXT NOT NULL DEFAULT 'unknown'")
    ensure_column(cursor, "audit_events", "resource_id", "TEXT")
    ensure_column(cursor, "audit_events", "company_id", "INTEGER")
    ensure_column(cursor, "audit_events", "status", "TEXT NOT NULL DEFAULT 'success'")
    ensure_column(cursor, "audit_events", "request_id", "TEXT")
    ensure_column(cursor, "audit_events", "http_method", "TEXT")
    ensure_column(cursor, "audit_events", "http_path", "TEXT")
    ensure_column(cursor, "audit_events", "client_ip", "TEXT")
    ensure_column(cursor, "audit_events", "user_agent", "TEXT")
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


def _create_contact_company_profiles(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_company_profiles (
                contact_id TEXT NOT NULL REFERENCES contacts(contact_id),
                company_id INTEGER NOT NULL,
                contact_name TEXT,
                last_classification TEXT,
                last_interaction_at TIMESTAMPTZ,
                conversation_summary TEXT,
                pipeline_stage TEXT,
                next_action TEXT,
                lead_temperature TEXT,
                customer_profile TEXT,
                notes TEXT,
                updated_at TIMESTAMPTZ,
                PRIMARY KEY (contact_id, company_id)
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_company_profiles (
                contact_id TEXT NOT NULL,
                company_id INTEGER NOT NULL,
                contact_name TEXT,
                last_classification TEXT,
                last_interaction_at TIMESTAMP,
                conversation_summary TEXT,
                pipeline_stage TEXT,
                next_action TEXT,
                lead_temperature TEXT,
                customer_profile TEXT,
                notes TEXT,
                updated_at TIMESTAMP,
                PRIMARY KEY (contact_id, company_id),
                FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "contact_company_profiles", "contact_name", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "last_classification", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "last_interaction_at", "TIMESTAMP")
    ensure_column(cursor, "contact_company_profiles", "conversation_summary", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "pipeline_stage", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "next_action", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "lead_temperature", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "customer_profile", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "notes", "TEXT")
    ensure_column(cursor, "contact_company_profiles", "updated_at", "TIMESTAMP")

    ensure_column(cursor, "contacts", "last_classification", "TEXT")
    ensure_column(cursor, "contacts", "last_interaction_at", "TIMESTAMP")
    ensure_column(cursor, "contacts", "conversation_summary", "TEXT")
    ensure_column(cursor, "contacts", "pipeline_stage", "TEXT")
    ensure_column(cursor, "contacts", "next_action", "TEXT")
    ensure_column(cursor, "contacts", "lead_temperature", "TEXT")
    ensure_column(cursor, "contacts", "customer_profile", "TEXT")
    ensure_column(cursor, "contacts", "notes", "TEXT")
    ensure_column(cursor, "contacts", "updated_at", "TIMESTAMP")
    ensure_column(cursor, "contacts", "company_id", "INTEGER")

    profile_columns = (
        "contact_id, company_id, contact_name, last_classification, last_interaction_at, "
        "conversation_summary, pipeline_stage, next_action, lead_temperature, customer_profile, notes, updated_at"
    )
    profile_select = (
        "SELECT contact_id, company_id, contact_name, last_classification, last_interaction_at, "
        "conversation_summary, pipeline_stage, next_action, lead_temperature, customer_profile, notes, updated_at "
        "FROM contacts WHERE company_id IS NOT NULL"
    )
    if get_database_backend() == "postgres":
        cursor.execute(
            f"""
            INSERT INTO contact_company_profiles ({profile_columns})
            {profile_select}
            ON CONFLICT (contact_id, company_id) DO NOTHING
            """
        )
    else:
        cursor.execute(
            f"""
            INSERT OR IGNORE INTO contact_company_profiles ({profile_columns})
            {profile_select}
            """
        )


def _create_contact_company_profile_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contact_company_profiles_company_contact
        ON contact_company_profiles (company_id, contact_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contact_company_profiles_company_updated_at
        ON contact_company_profiles (company_id, updated_at)
        """
    )


def _add_audit_event_context_columns(cursor: Any) -> None:
    _create_audit_tables(cursor)


def _create_audit_event_context_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_request_id
        ON audit_events (request_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_actor_created_at
        ON audit_events (actor_id, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_events_http_path_created_at
        ON audit_events (http_path, created_at)
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
                scheduling_profile_key TEXT,
                customer_profile TEXT,
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
                scheduling_profile_key TEXT,
                customer_profile TEXT,
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
    ensure_column(cursor, "appointment_bookings", "scheduling_profile_key", "TEXT")
    ensure_column(cursor, "appointment_bookings", "customer_profile", "TEXT")
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


def _create_queue_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_queue_entries (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                desired_title TEXT NOT NULL,
                scheduling_profile_key TEXT,
                customer_profile TEXT,
                request_summary TEXT,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                earliest_starts_at TIMESTAMPTZ,
                latest_starts_at TIMESTAMPTZ,
                preferred_periods_json TEXT,
                priority_tier TEXT NOT NULL DEFAULT 'normal',
                priority_score INTEGER NOT NULL DEFAULT 0,
                accepts_short_notice INTEGER NOT NULL DEFAULT 1,
                original_booking_id BIGINT REFERENCES appointment_bookings(id),
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'manual',
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_queue_offers (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                queue_entry_id BIGINT NOT NULL REFERENCES appointment_queue_entries(id),
                released_booking_id BIGINT REFERENCES appointment_bookings(id),
                outbound_message_id BIGINT REFERENCES outbound_messages(id),
                new_booking_id BIGINT REFERENCES appointment_bookings(id),
                offered_starts_at TIMESTAMPTZ NOT NULL,
                offered_ends_at TIMESTAMPTZ NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                status TEXT NOT NULL DEFAULT 'pending',
                response_deadline_at TIMESTAMPTZ,
                follow_up_after TIMESTAMPTZ,
                responded_at TIMESTAMPTZ,
                response_notes TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_queue_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                contact_id TEXT NOT NULL,
                desired_title TEXT NOT NULL,
                scheduling_profile_key TEXT,
                customer_profile TEXT,
                request_summary TEXT,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                earliest_starts_at TIMESTAMP,
                latest_starts_at TIMESTAMP,
                preferred_periods_json TEXT,
                priority_tier TEXT NOT NULL DEFAULT 'normal',
                priority_score INTEGER NOT NULL DEFAULT 0,
                accepts_short_notice INTEGER NOT NULL DEFAULT 1,
                original_booking_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'manual',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (original_booking_id) REFERENCES appointment_bookings(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointment_queue_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                queue_entry_id INTEGER NOT NULL,
                released_booking_id INTEGER,
                outbound_message_id INTEGER,
                new_booking_id INTEGER,
                offered_starts_at TIMESTAMP NOT NULL,
                offered_ends_at TIMESTAMP NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                status TEXT NOT NULL DEFAULT 'pending',
                response_deadline_at TIMESTAMP,
                follow_up_after TIMESTAMP,
                responded_at TIMESTAMP,
                response_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (queue_entry_id) REFERENCES appointment_queue_entries(id),
                FOREIGN KEY (released_booking_id) REFERENCES appointment_bookings(id),
                FOREIGN KEY (outbound_message_id) REFERENCES outbound_messages(id),
                FOREIGN KEY (new_booking_id) REFERENCES appointment_bookings(id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "appointment_queue_entries", "company_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_entries", "contact_id", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cursor, "appointment_queue_entries", "desired_title", "TEXT NOT NULL DEFAULT 'Agendamento'")
    ensure_column(cursor, "appointment_queue_entries", "scheduling_profile_key", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "customer_profile", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "request_summary", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "timezone", "TEXT NOT NULL DEFAULT 'UTC'")
    ensure_column(cursor, "appointment_queue_entries", "earliest_starts_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_entries", "latest_starts_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_entries", "preferred_periods_json", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "priority_tier", "TEXT NOT NULL DEFAULT 'normal'")
    ensure_column(cursor, "appointment_queue_entries", "priority_score", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "appointment_queue_entries", "accepts_short_notice", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "appointment_queue_entries", "original_booking_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_entries", "status", "TEXT NOT NULL DEFAULT 'active'")
    ensure_column(cursor, "appointment_queue_entries", "source", "TEXT NOT NULL DEFAULT 'manual'")
    ensure_column(cursor, "appointment_queue_entries", "notes", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "appointment_queue_entries", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    ensure_column(cursor, "appointment_queue_offers", "company_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_offers", "queue_entry_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_offers", "released_booking_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_offers", "outbound_message_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_offers", "new_booking_id", "INTEGER")
    ensure_column(cursor, "appointment_queue_offers", "offered_starts_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "offered_ends_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "timezone", "TEXT NOT NULL DEFAULT 'UTC'")
    ensure_column(cursor, "appointment_queue_offers", "status", "TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(cursor, "appointment_queue_offers", "response_deadline_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "follow_up_after", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "responded_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "response_notes", "TEXT")
    ensure_column(cursor, "appointment_queue_offers", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "appointment_queue_offers", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")


def _add_booking_outcome_columns(cursor: Any) -> None:
    ensure_column = _ensure_postgres_column if get_database_backend() == "postgres" else _ensure_sqlite_column

    ensure_column(cursor, "appointment_bookings", "booking_outcome", "TEXT")
    ensure_column(cursor, "appointment_bookings", "booking_outcome_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_bookings", "booking_outcome_notes", "TEXT")
    ensure_column(cursor, "appointment_bookings", "cancellation_reason", "TEXT")


def _create_analytics_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bookings_outcome_company
        ON appointment_bookings (company_id, booking_outcome, starts_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bookings_status_starts_at
        ON appointment_bookings (status, starts_at, company_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_booking_status
        ON customer_reminders (appointment_booking_id, status, audience)
        """
    )


def _create_queue_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_entries_company_status_priority
        ON appointment_queue_entries (company_id, status, priority_score, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_entries_original_booking_status
        ON appointment_queue_entries (original_booking_id, status)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_offers_company_status_starts_at
        ON appointment_queue_offers (company_id, status, offered_starts_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_offers_released_booking_status
        ON appointment_queue_offers (released_booking_id, status, created_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_offers_entry_status
        ON appointment_queue_offers (queue_entry_id, status, created_at)
        """
    )


def _create_billing_tables(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_plans (
                id BIGSERIAL PRIMARY KEY,
                plan_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                price_monthly_cents INTEGER NOT NULL,
                price_annual_monthly_cents INTEGER,
                limits_json TEXT NOT NULL DEFAULT '{}',
                features_json TEXT NOT NULL DEFAULT '{}',
                display_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'trial',
                billing_cycle TEXT NOT NULL DEFAULT 'monthly',
                trial_ends_at TIMESTAMPTZ,
                current_period_start TIMESTAMPTZ NOT NULL,
                current_period_end TIMESTAMPTZ NOT NULL,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                cancelled_at TIMESTAMPTZ,
                cancel_reason TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_usage_records (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                subscription_id BIGINT,
                metric TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                recorded_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                price_monthly_cents INTEGER NOT NULL,
                price_annual_monthly_cents INTEGER,
                limits_json TEXT NOT NULL DEFAULT '{}',
                features_json TEXT NOT NULL DEFAULT '{}',
                display_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'trial',
                billing_cycle TEXT NOT NULL DEFAULT 'monthly',
                trial_ends_at TIMESTAMP,
                current_period_start TIMESTAMP NOT NULL,
                current_period_end TIMESTAMP NOT NULL,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                cancelled_at TIMESTAMP,
                cancel_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_usage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                subscription_id INTEGER,
                metric TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                period_start TIMESTAMP NOT NULL,
                period_end TIMESTAMP NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "billing_subscriptions", "company_id", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "billing_subscriptions", "plan_key", "TEXT NOT NULL DEFAULT 'essencial'")
    ensure_column(cursor, "billing_subscriptions", "status", "TEXT NOT NULL DEFAULT 'trial'")
    ensure_column(cursor, "billing_subscriptions", "billing_cycle", "TEXT NOT NULL DEFAULT 'monthly'")
    ensure_column(cursor, "billing_subscriptions", "trial_ends_at", "TIMESTAMP")
    ensure_column(cursor, "billing_subscriptions", "current_period_start", "TIMESTAMP")
    ensure_column(cursor, "billing_subscriptions", "current_period_end", "TIMESTAMP")
    ensure_column(cursor, "billing_subscriptions", "stripe_customer_id", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "stripe_subscription_id", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "cancelled_at", "TIMESTAMP")
    ensure_column(cursor, "billing_subscriptions", "cancel_reason", "TEXT")

    ensure_column(cursor, "billing_usage_records", "company_id", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "billing_usage_records", "subscription_id", "INTEGER")
    ensure_column(cursor, "billing_usage_records", "metric", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cursor, "billing_usage_records", "quantity", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "billing_usage_records", "period_start", "TIMESTAMP")
    ensure_column(cursor, "billing_usage_records", "period_end", "TIMESTAMP")


def _create_billing_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_subscriptions_company_status
        ON billing_subscriptions (company_id, status)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_company_metric_period
        ON billing_usage_records (company_id, metric, period_start, period_end)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_subscription_metric
        ON billing_usage_records (subscription_id, metric, recorded_at)
        """
    )


def _create_inbound_message_receipts(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_message_receipts (
                id BIGSERIAL PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_message_id TEXT NOT NULL,
                company_id INTEGER,
                contact_id TEXT,
                payload_mode TEXT,
                status TEXT NOT NULL DEFAULT 'processing',
                last_error TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, provider_message_id)
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_message_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                provider_message_id TEXT NOT NULL,
                company_id INTEGER,
                contact_id TEXT,
                payload_mode TEXT,
                status TEXT NOT NULL DEFAULT 'processing',
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, provider_message_id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "inbound_message_receipts", "company_id", "INTEGER")
    ensure_column(cursor, "inbound_message_receipts", "contact_id", "TEXT")
    ensure_column(cursor, "inbound_message_receipts", "payload_mode", "TEXT")
    ensure_column(cursor, "inbound_message_receipts", "status", "TEXT NOT NULL DEFAULT 'processing'")
    ensure_column(cursor, "inbound_message_receipts", "last_error", "TEXT")
    ensure_column(cursor, "inbound_message_receipts", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "inbound_message_receipts", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")


def _create_inbound_message_receipt_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_inbound_receipts_company_status
        ON inbound_message_receipts (company_id, status)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_inbound_receipts_contact_status
        ON inbound_message_receipts (contact_id, status)
        """
    )


def _create_company_contexts(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_contexts (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER,
                company_slug TEXT NOT NULL,
                context_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                UNIQUE(company_slug)
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                company_slug TEXT NOT NULL,
                context_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                UNIQUE(company_slug)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "company_contexts", "company_id", "INTEGER")
    ensure_column(cursor, "company_contexts", "schema_version", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "company_contexts", "revision", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "company_contexts", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "company_contexts", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "company_contexts", "updated_by", "TEXT")


def _create_company_context_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_company_contexts_slug
        ON company_contexts (company_slug)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_company_contexts_company
        ON company_contexts (company_id)
        """
    )


def _create_whatsapp_channel_bindings(cursor: Any) -> None:
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_channel_bindings (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                phone_number_id TEXT NOT NULL,
                display_phone_number TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT
            )
            """
        )
        ensure_column = _ensure_postgres_column
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_channel_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                phone_number_id TEXT NOT NULL,
                display_phone_number TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by TEXT,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            )
            """
        )
        ensure_column = _ensure_sqlite_column

    ensure_column(cursor, "whatsapp_channel_bindings", "display_phone_number", "TEXT")
    ensure_column(cursor, "whatsapp_channel_bindings", "active", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(cursor, "whatsapp_channel_bindings", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "whatsapp_channel_bindings", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(cursor, "whatsapp_channel_bindings", "updated_by", "TEXT")


def _create_whatsapp_channel_binding_indexes(cursor: Any) -> None:
    # A phone_number_id may appear at most once among *active* bindings, so an
    # inbound Meta event always resolves to exactly one company. Deactivated
    # rows are kept for history and are excluded from the constraint.
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_bindings_active_phone
        ON whatsapp_channel_bindings (phone_number_id)
        WHERE active = 1
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_whatsapp_bindings_company
        ON whatsapp_channel_bindings (company_id, active)
        """
    )


def _create_identity_tables(cursor: Any) -> None:
    """Create the human identity directory and revocable browser sessions."""
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS saas_users (
                id BIGSERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                email_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                email_verified_at TIMESTAMPTZ,
                last_login_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_memberships (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES saas_users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                invited_by_user_id BIGINT REFERENCES saas_users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, user_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_invitations (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                email_normalized TEXT NOT NULL,
                role TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                invited_by_user_id BIGINT NOT NULL REFERENCES saas_users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                accepted_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES saas_users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token_hash TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMPTZ,
                user_agent TEXT,
                ip_address TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES saas_users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS saas_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                email_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                email_verified_at TIMESTAMP,
                last_login_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                invited_by_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, user_id),
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES saas_users(id) ON DELETE CASCADE,
                FOREIGN KEY (invited_by_user_id) REFERENCES saas_users(id) ON DELETE SET NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                email_normalized TEXT NOT NULL,
                role TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                invited_by_user_id INTEGER NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                accepted_at TIMESTAMP,
                revoked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
                FOREIGN KEY (invited_by_user_id) REFERENCES saas_users(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token_hash TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP,
                user_agent TEXT,
                ip_address TEXT,
                FOREIGN KEY (user_id) REFERENCES saas_users(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TIMESTAMP NOT NULL,
                consumed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES saas_users(id) ON DELETE CASCADE
            )
            """
        )


def _create_identity_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memberships_user_active
        ON company_memberships (user_id, active, company_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memberships_company_role
        ON company_memberships (company_id, active, role)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invitations_company_email
        ON user_invitations (company_id, email_normalized, expires_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_user_expiry
        ON user_sessions (user_id, expires_at, revoked_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_password_resets_user_expiry
        ON password_reset_tokens (user_id, expires_at, consumed_at)
        """
    )


def _create_reliable_job_state(cursor: Any) -> None:
    """Add portable claims, retries, dead letters and distributed worker leases."""
    ensure_column = (
        _ensure_postgres_column
        if get_database_backend() == "postgres"
        else _ensure_sqlite_column
    )
    ensure_column(cursor, "outbound_messages", "channel", "TEXT")
    ensure_column(cursor, "outbound_messages", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "outbound_messages", "max_attempts", "INTEGER NOT NULL DEFAULT 5")
    ensure_column(cursor, "outbound_messages", "next_attempt_at", "TIMESTAMP")
    ensure_column(cursor, "outbound_messages", "claimed_at", "TIMESTAMP")
    ensure_column(cursor, "outbound_messages", "claim_token", "TEXT")
    ensure_column(cursor, "outbound_messages", "last_error", "TEXT")
    ensure_column(cursor, "outbound_messages", "provider_message_id", "TEXT")
    ensure_column(cursor, "outbound_messages", "provider_response_json", "TEXT")
    ensure_column(cursor, "outbound_messages", "failed_at", "TIMESTAMP")

    timestamp_type = "TIMESTAMPTZ" if get_database_backend() == "postgres" else "TIMESTAMP"
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS worker_leases (
            lease_name TEXT PRIMARY KEY,
            owner_token TEXT NOT NULL,
            expires_at {timestamp_type} NOT NULL,
            updated_at {timestamp_type} DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _create_reliable_job_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbound_dispatch_claim
        ON outbound_messages (status, next_attempt_at, claimed_at, id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbound_company_dispatch
        ON outbound_messages (company_id, status, next_attempt_at, id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_worker_leases_expiry
        ON worker_leases (expires_at)
        """
    )


def _create_reliable_reminder_state(cursor: Any) -> None:
    ensure_column = (
        _ensure_postgres_column
        if get_database_backend() == "postgres"
        else _ensure_sqlite_column
    )
    ensure_column(cursor, "customer_reminders", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "customer_reminders", "max_attempts", "INTEGER NOT NULL DEFAULT 5")
    ensure_column(cursor, "customer_reminders", "next_attempt_at", "TIMESTAMP")
    ensure_column(cursor, "customer_reminders", "claimed_at", "TIMESTAMP")
    ensure_column(cursor, "customer_reminders", "claim_token", "TEXT")
    ensure_column(cursor, "customer_reminders", "last_error", "TEXT")
    ensure_column(cursor, "customer_reminders", "failed_at", "TIMESTAMP")


def _create_reliable_reminder_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_dispatch_claim
        ON customer_reminders (
            status,
            audience,
            next_attempt_at,
            remind_at,
            claimed_at,
            id
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_company_dispatch_claim
        ON customer_reminders (
            company_id,
            status,
            next_attempt_at,
            remind_at,
            id
        )
        """
    )


def _create_billing_gateway_state(cursor: Any) -> None:
    ensure_column = (
        _ensure_postgres_column
        if get_database_backend() == "postgres"
        else _ensure_sqlite_column
    )
    ensure_column(cursor, "billing_plans", "currency", "TEXT NOT NULL DEFAULT 'brl'")
    ensure_column(cursor, "billing_plans", "stripe_product_id", "TEXT")
    ensure_column(cursor, "billing_plans", "stripe_monthly_price_id", "TEXT")
    ensure_column(cursor, "billing_plans", "stripe_annual_price_id", "TEXT")

    ensure_column(cursor, "billing_subscriptions", "gateway", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "stripe_price_id", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "payment_status", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "cancel_at_period_end", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cursor, "billing_subscriptions", "grace_period_ends_at", "TIMESTAMP")
    ensure_column(cursor, "billing_subscriptions", "last_invoice_id", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "gateway_event_created", "INTEGER")
    ensure_column(cursor, "billing_subscriptions", "gateway_event_id", "TEXT")
    ensure_column(cursor, "billing_subscriptions", "gateway_updated_at", "TIMESTAMP")

    if get_database_backend() == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_webhook_events (
                id BIGSERIAL PRIMARY KEY,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_created INTEGER NOT NULL,
                livemode INTEGER NOT NULL DEFAULT 0,
                company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                attempt_count INTEGER NOT NULL DEFAULT 1,
                last_error TEXT,
                processed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (provider, event_id)
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS billing_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_created INTEGER NOT NULL,
                livemode INTEGER NOT NULL DEFAULT 0,
                company_id INTEGER,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                attempt_count INTEGER NOT NULL DEFAULT 1,
                last_error TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (provider, event_id),
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
            )
            """
        )


def _create_billing_gateway_indexes(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_plan_stripe_monthly_price
        ON billing_plans (stripe_monthly_price_id)
        WHERE stripe_monthly_price_id IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_plan_stripe_annual_price
        ON billing_plans (stripe_annual_price_id)
        WHERE stripe_annual_price_id IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_subscription_stripe_id
        ON billing_subscriptions (stripe_subscription_id)
        WHERE stripe_subscription_id IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_billing_subscription_customer
        ON billing_subscriptions (stripe_customer_id, company_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_billing_webhook_status_created
        ON billing_webhook_events (status, created_at)
        """
    )


def _constraint_exists(cursor: Any, *, table: str, constraint: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conname = ?
          AND conrelid = ?::regclass
        """,
        (constraint, table),
    )
    return cursor.fetchone() is not None


def _create_tenant_integrity_guards(cursor: Any) -> None:
    """Reject orphan and cross-tenant references at the database boundary."""
    backend = get_database_backend()
    for table in TENANT_REFERENCE_TABLES:
        # Identifier comes from immutable migration metadata.
        orphan_query = f"""
            SELECT COUNT(*) AS total
            FROM {table} AS tenant_row
            WHERE tenant_row.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM companies AS company
                  WHERE company.id = tenant_row.company_id
              )
            """  # nosec B608
        cursor.execute(orphan_query)
        orphan_count = int(cursor.fetchone()["total"] or 0)
        if orphan_count:
            raise RuntimeError(
                f"Tenant integrity migration blocked: {table} has "
                f"{orphan_count} orphan company reference(s)."
            )

        if backend == "postgres":
            constraint = f"fk_{table}_company"
            if not _constraint_exists(
                cursor,
                table=table,
                constraint=constraint,
            ):
                cursor.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD CONSTRAINT {constraint}
                    FOREIGN KEY (company_id)
                    REFERENCES companies(id)
                    ON DELETE RESTRICT
                    NOT VALID
                    """
                )
                cursor.execute(
                    f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}"
                )
            continue

        trigger_prefix = f"trg_{table}_company"
        # Identifiers come from immutable migration metadata.
        insert_company_trigger = f"""
            CREATE TRIGGER IF NOT EXISTS {trigger_prefix}_insert
            BEFORE INSERT ON {table}
            WHEN NEW.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM companies WHERE id = NEW.company_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'invalid tenant company reference');
            END
            """  # nosec B608
        cursor.execute(insert_company_trigger)
        update_company_trigger = f"""
            CREATE TRIGGER IF NOT EXISTS {trigger_prefix}_update
            BEFORE UPDATE OF company_id ON {table}
            WHEN NEW.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM companies WHERE id = NEW.company_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'invalid tenant company reference');
            END
            """  # nosec B608
        cursor.execute(update_company_trigger)

    parent_tables = {parent for _child, _column, parent in TENANT_REFERENTIAL_LINKS}
    for parent in parent_tables:
        cursor.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{parent}_id_company
            ON {parent} (id, company_id)
            """
        )

    for child, column, parent in TENANT_REFERENTIAL_LINKS:
        # Identifiers come from immutable migration metadata.
        tenant_mismatch_query = f"""
            SELECT COUNT(*) AS total
            FROM {child} AS child_row
            JOIN {parent} AS parent_row
              ON parent_row.id = child_row.{column}
            WHERE child_row.{column} IS NOT NULL
              AND child_row.company_id IS NOT NULL
              AND (
                  parent_row.company_id IS NULL
                  OR parent_row.company_id <> child_row.company_id
              )
            """  # nosec B608
        cursor.execute(tenant_mismatch_query)
        mismatch_count = int(cursor.fetchone()["total"] or 0)
        if mismatch_count:
            raise RuntimeError(
                f"Tenant integrity migration blocked: {child}.{column} has "
                f"{mismatch_count} cross-tenant reference(s)."
            )

        suffix = f"{child}_{column}_tenant"
        if backend == "postgres":
            constraint = f"fk_{suffix}"
            if not _constraint_exists(
                cursor,
                table=child,
                constraint=constraint,
            ):
                cursor.execute(
                    f"""
                    ALTER TABLE {child}
                    ADD CONSTRAINT {constraint}
                    FOREIGN KEY ({column}, company_id)
                    REFERENCES {parent} (id, company_id)
                    ON DELETE RESTRICT
                    NOT VALID
                    """
                )
                cursor.execute(
                    f"ALTER TABLE {child} VALIDATE CONSTRAINT {constraint}"
                )
            continue

        insert_reference_trigger = f"""
            CREATE TRIGGER IF NOT EXISTS trg_{suffix}_insert
            BEFORE INSERT ON {child}
            WHEN NEW.{column} IS NOT NULL
              AND NEW.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM {parent}
                  WHERE id = NEW.{column}
                    AND company_id = NEW.company_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'cross-tenant reference rejected');
            END
            """  # nosec B608
        cursor.execute(insert_reference_trigger)
        update_reference_trigger = f"""
            CREATE TRIGGER IF NOT EXISTS trg_{suffix}_update
            BEFORE UPDATE OF {column}, company_id ON {child}
            WHEN NEW.{column} IS NOT NULL
              AND NEW.company_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM {parent}
                  WHERE id = NEW.{column}
                    AND company_id = NEW.company_id
              )
            BEGIN
                SELECT RAISE(ABORT, 'cross-tenant reference rejected');
            END
            """  # nosec B608
        cursor.execute(update_reference_trigger)


def _import_company_context_seed_files(cursor: Any) -> None:
    """Idempotently import the file-based seed contexts into the database.

    Runs inside the migration transaction. Existing rows are never overwritten,
    so a newer database context always wins over an older seed file.
    """
    from app.core.company_context import import_seed_contexts_with_cursor

    import_seed_contexts_with_cursor(cursor)


SQLITE_ID_TABLES_BILLING = {"billing_plans", "billing_subscriptions", "billing_usage_records"}


def _repair_queue_columns(cursor: Any) -> None:
    """Ensure all queue columns exist for databases created before migration 011."""
    ensure_column = _ensure_postgres_column if get_database_backend() == "postgres" else _ensure_sqlite_column

    ensure_column(cursor, "appointment_queue_entries", "scheduling_profile_key", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "customer_profile", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "request_summary", "TEXT")
    ensure_column(cursor, "appointment_queue_entries", "source", "TEXT NOT NULL DEFAULT 'manual'")
    ensure_column(cursor, "appointment_queue_entries", "notes", "TEXT")


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
    ("011_queue_tables", "Create appointment queue tables", _create_queue_tables),
    ("012_queue_indexes", "Create appointment queue indexes", _create_queue_indexes),
    ("013_booking_outcome", "Add booking outcome tracking columns", _add_booking_outcome_columns),
    ("014_analytics_indexes", "Create analytics-oriented indexes", _create_analytics_indexes),
    ("015_billing_tables", "Create billing plans, subscriptions and usage tables", _create_billing_tables),
    ("016_billing_indexes", "Create billing indexes", _create_billing_indexes),
    ("017_repair_queue_columns", "Ensure queue entry columns exist", _repair_queue_columns),
    ("018_inbound_message_receipts", "Create inbound message receipts table", _create_inbound_message_receipts),
    ("019_inbound_message_receipt_indexes", "Create inbound message receipt indexes", _create_inbound_message_receipt_indexes),
    ("020_audit_event_context_columns", "Add admin and HTTP context columns to audit events", _add_audit_event_context_columns),
    ("021_audit_event_context_indexes", "Create audit event context indexes", _create_audit_event_context_indexes),
    ("022_contact_company_profiles", "Create tenant-scoped contact profiles", _create_contact_company_profiles),
    ("023_contact_company_profile_indexes", "Create contact profile indexes", _create_contact_company_profile_indexes),
    ("024_company_contexts", "Create durable company context table", _create_company_contexts),
    ("025_company_context_indexes", "Create company context indexes", _create_company_context_indexes),
    ("026_company_context_seed_import", "Import file-based company contexts", _import_company_context_seed_files),
    ("027_whatsapp_channel_bindings", "Create WhatsApp channel to company bindings", _create_whatsapp_channel_bindings),
    ("028_whatsapp_channel_binding_indexes", "Create WhatsApp channel binding indexes", _create_whatsapp_channel_binding_indexes),
    ("029_human_identity", "Create users, memberships, invitations and sessions", _create_identity_tables),
    ("030_human_identity_indexes", "Create identity and session indexes", _create_identity_indexes),
    ("031_reliable_job_state", "Add outbound claims, retries and worker leases", _create_reliable_job_state),
    ("032_reliable_job_indexes", "Create outbound and worker lease indexes", _create_reliable_job_indexes),
    ("033_billing_gateway_state", "Add Stripe billing state and webhook ledger", _create_billing_gateway_state),
    ("034_billing_gateway_indexes", "Create Stripe billing and event indexes", _create_billing_gateway_indexes),
    ("035_tenant_integrity", "Enforce tenant and cross-tenant references", _create_tenant_integrity_guards),
    ("036_reliable_reminder_state", "Add reminder claims, retries and dead letters", _create_reliable_reminder_state),
    ("037_reliable_reminder_indexes", "Create reminder dispatch claim indexes", _create_reliable_reminder_indexes),
]

# As migrations 038+ vivem em módulos próprios para que este arquivo não cresça
# sem limite. A ordem de concatenação é a ordem de aplicação, e a numeração é
# contínua (038-066), então `_assert_migration_versions_are_ordered` abaixo
# falha na importação se um módulo for inserido fora de ordem ou se duas
# migrations colidirem numa mesma versão.
#
# O import é feito aqui, no fim do módulo, e não no topo: `schema_utils`
# consulta `get_database_backend()`, definido neste arquivo, e um import no
# topo fecharia o ciclo.
from app.storage.migrations_automation import (  # noqa: E402
    AUTOMATION_ID_TABLES,
    AUTOMATION_MIGRATIONS,
)
from app.storage.migrations_hardening import HARDENING_MIGRATIONS  # noqa: E402
from app.storage.migrations_identity import (  # noqa: E402
    IDENTITY_ID_TABLES,
    IDENTITY_MIGRATIONS,
)
from app.storage.migrations_platform import (  # noqa: E402
    PLATFORM_ID_TABLES,
    PLATFORM_MIGRATIONS,
)

MIGRATIONS.extend(PLATFORM_MIGRATIONS)
MIGRATIONS.extend(IDENTITY_MIGRATIONS)
MIGRATIONS.extend(HARDENING_MIGRATIONS)
MIGRATIONS.extend(AUTOMATION_MIGRATIONS)

# Tabelas com PK autoincremental precisam de `RETURNING id` no PostgreSQL para
# que `cursor.lastrowid` funcione — no SQLite ele vem de graça, no PostgreSQL
# não existe. Cada módulo de migration declara as suas, e é aqui que entram no
# registro: sem isso todo INSERT nessas tabelas devolveria `lastrowid = None`
# no PostgreSQL e o código chamador gravaria um id nulo, ou quebraria, apenas
# no backend de produção.
SQLITE_ID_TABLES.update(PLATFORM_ID_TABLES)
SQLITE_ID_TABLES.update(IDENTITY_ID_TABLES)
SQLITE_ID_TABLES.update(AUTOMATION_ID_TABLES)


def _assert_migration_versions_are_ordered() -> None:
    """Falha na importação se a numeração das migrations estiver inconsistente.

    Uma versão duplicada é o erro mais caro possível aqui: a segunda nunca
    roda, porque `schema_migrations` já registrou aquela versão, e a
    divergência de esquema só aparece em produção. Verificar na importação
    custa microssegundos e torna o erro impossível de mesclar.
    """
    versions = [version for version, _name, _fn in MIGRATIONS]
    duplicates = {version for version in versions if versions.count(version) > 1}
    if duplicates:
        raise RuntimeError(f"Duplicate migration versions: {sorted(duplicates)}")
    if versions != sorted(versions):
        out_of_order = [
            (previous, current)
            for previous, current in itertools.pairwise(versions)
            if previous > current
        ]
        raise RuntimeError(f"Migration versions are out of order: {out_of_order}")


_assert_migration_versions_are_ordered()


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


def _acquire_migration_lock(cursor: Any) -> None:
    """Serialize migrations across processes on Postgres.

    The API and the worker boot concurrently and both call ``init_db()``. The
    lock is transaction-scoped, so it is released by the commit/rollback in
    ``init_db``. SQLite acquires its write lock before reading the migration
    ledger in ``init_db``; a deferred transaction would allow two processes to
    read the same stale ledger and then race while recording a version.
    """
    if get_database_backend() != "postgres":
        return
    cursor.execute("SELECT pg_advisory_xact_lock(?)", (MIGRATION_ADVISORY_LOCK_KEY,))
    cursor.fetchone()


def _run_migrations(cursor: Any) -> None:
    _acquire_migration_lock(cursor)
    _ensure_schema_migrations_table(cursor)
    applied_versions = _get_applied_migration_versions(cursor)

    for version, name, migration_fn in MIGRATIONS:
        if version in applied_versions:
            continue
        migration_fn(cursor)
        _record_migration(cursor, version, name)


def init_db() -> None:
    # A failed migration must roll back and release the connection; on Postgres
    # that also drops the transaction-scoped advisory lock, so a crashed boot
    # cannot block every other process from migrating.
    with db_transaction() as connection:
        cursor = connection.cursor()
        if get_database_backend() == "sqlite":
            # SQLite transactions are deferred by default. Acquire the reserved
            # write lock before inspecting schema_migrations so concurrent
            # Uvicorn workers cannot both decide that the same migration is
            # pending. The second worker waits, then observes the committed
            # ledger and skips already-applied migrations.
            cursor.execute("BEGIN IMMEDIATE")
        _run_migrations(cursor)
