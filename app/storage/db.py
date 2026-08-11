import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
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


# Parâmetro que aparece **apenas** dentro de `IS NULL`. O PostgreSQL não
# consegue inferir o tipo nesse caso e responde
# `could not determine data type of parameter $N`.
#
# O repositório usa esse padrão 67 vezes para tornar um filtro opcional
# (`WHERE (? IS NULL OR coluna = ?)`), então derrubava praticamente toda
# listagem filtrável no backend de produção. O SQLite aceita, e por isso a
# suíte nunca viu.
#
# `::text` é suficiente: o único uso do parâmetro é a checagem de nulidade, e
# o `= %s` ao lado infere o tipo da própria coluna. A correção fica aqui, num
# lugar só — espalhar cast por 67 pontos de chamada seria esquecido no próximo
# SELECT escrito.
_UNTYPED_NULL_CHECK = re.compile(r"%s(\s+IS\s+(?:NOT\s+)?NULL)", re.IGNORECASE)


def _cast_optional_filter_parameters(query: str) -> str:
    return _UNTYPED_NULL_CHECK.sub(r"%s::text\1", query)


def _normalize_postgres_row(row: Any) -> Any:
    """Faz o PostgreSQL devolver os mesmos tipos que o SQLite.

    Duas divergências, ambas descobertas rodando a suíte contra PostgreSQL
    real e invisíveis em SQLite:

    1. **Timestamp vira `datetime`.** O SQLite entrega texto. Todo código que
       compara, serializa ou valida com Pydantic quebrava — `CompanyResponse`
       recusava o `created_at` do próprio banco.
    2. **O fuso vem o da sessão.** Comparar ISO como string entre offsets
       diferentes está errado: `13:36-03:00` e `15:36+00:00` são o mesmo
       instante, mas a comparação textual diz que o primeiro é maior. Por isso
       a conversão é para **UTC** antes do `isoformat()`.

    `Decimal` acompanha pela mesma razão: o SQLite devolve `float`.
    """
    if row is None:
        return None
    if not isinstance(row, dict):
        return row

    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            normalized[key] = aware.astimezone(UTC).isoformat()
        elif isinstance(value, date):
            normalized[key] = value.isoformat()
        elif isinstance(value, Decimal):
            normalized[key] = float(value)
        else:
            normalized[key] = value
    return normalized


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
    normalized_query = _cast_optional_filter_parameters(normalized_query)

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
        return _normalize_postgres_row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [_normalize_postgres_row(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)


class PostgresConnectionWrapper:
    def __init__(self, raw_connection: Any, pool: Any) -> None:
        self._connection = raw_connection
        self._pool = pool
        self._closed = False

    def cursor(self) -> PostgresCursorWrapper:
        return PostgresCursorWrapper(self._connection.cursor())

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        """Devolve a conexão ao pool **exatamente uma vez**.

        A idempotência não é conveniência: devolver duas vezes é pior que
        vazar. O pool passa a acreditar que tem uma conexão livre a mais do que
        realmente tem, e duas partes do código recebem o mesmo objeto — o
        segundo `putconn` corrompe a contabilidade do pool em vez de só
        desperdiçar um recurso.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.rollback()
        except Exception as exc:
            # A conexão precisa voltar ao pool de qualquer forma: uma conexão
            # vazada trava o pool inteiro. Mas falhar o rollback em silêncio
            # devolveria uma transação abortada, e a próxima operação naquela
            # conexão herdaria `current transaction is aborted`.
            logging.getLogger(__name__).warning(
                "postgres.connection.rollback_failed", extra={"error_type": type(exc).__name__}
            )
        finally:
            self._pool.putconn(self._connection)


# Protege a construção e o fechamento do pool único do processo (D-09).
_POSTGRES_POOL_LOCK = threading.Lock()


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
    """Devolve o pool único do processo, construindo-o no máximo uma vez.

    A checagem sem lock era uma corrida de verdade, não teórica: os handlers
    síncronos do FastAPI rodam num threadpool, então no cold start e a cada
    rolling update as primeiras requisições concorrentes entravam aqui juntas.
    Oito threads num barrier construíam **oito** pools; `close_postgres_pool()`
    alcançava apenas o último, e os outros sete seguravam backends e threads do
    psycopg_pool até o processo morrer. Num PostgreSQL gerenciado isso consome
    slots de `max_connections` de forma permanente.

    O padrão é double-checked locking: a leitura rápida fora do lock mantém o
    caminho quente barato, e a segunda checagem dentro dele é o que garante
    unicidade.
    """
    global _POSTGRES_POOL
    if _POSTGRES_POOL is not None:
        return _POSTGRES_POOL

    with _POSTGRES_POOL_LOCK:
        if _POSTGRES_POOL is not None:
            return _POSTGRES_POOL

        try:
            from psycopg_pool import ConnectionPool  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Postgres backend requires psycopg_pool. Install dependencies from requirements.txt."
            ) from exc

        # `open=True` explícito: o default do psycopg_pool vai virar `False` numa
        # versão futura, e herdar essa mudança em silêncio deixaria o pool fechado
        # — o processo subiria e só falharia na primeira consulta.
        _POSTGRES_POOL = ConnectionPool(**_get_postgres_connect_kwargs(), open=True)
        return _POSTGRES_POOL


def close_postgres_pool() -> None:
    global _POSTGRES_POOL
    with _POSTGRES_POOL_LOCK:
        if _POSTGRES_POOL is None:
            return
        try:
            _POSTGRES_POOL.close()
        finally:
            _POSTGRES_POOL = None


def get_connection() -> Any:
    backend = get_database_backend()
    if backend == "sqlite":
        connection = sqlite3.connect(get_runtime_database_path())
        connection.row_factory = sqlite3.Row
        # Integridade referencial ligada. O SQLite a deixa DESLIGADA por padrão
        # e por conexão, então sem esta linha ele aceita em silêncio o INSERT
        # órfão que o PostgreSQL recusa — e a suíte inteira fica cega para a
        # classe de defeito que só aparece em produção.
        #
        # Ligar isto exigiu fechar o I-01 primeiro: com as conexões vazando,
        # cada violação de FK levava uma conexão junto e dez delas esgotavam o
        # pool do PostgreSQL. Agora toda aquisição passa por `transaction()`,
        # `read_connection()` ou `connection_scope()`, que devolvem em `finally`.
        connection.execute("PRAGMA foreign_keys = ON")
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


@contextmanager
def connection_scope() -> Iterator[Any]:
    """Aquisição segura com o commit sob controle de quem chama.

    Existe para o caso em que `transaction()` mudaria a semântica: um laço que
    comita por iteração, como o claim de job, precisa que cada tentativa seja
    durável antes da próxima. Um commit único no fim transformaria N decisões
    independentes numa só.

    A garantia que este gerenciador dá é só a devolução — quem usa continua
    responsável por `commit()` e por `rollback()`.
    """
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def read_connection() -> Iterator[Any]:
    """Conexão para leitura. Devolvida em `finally`, sem commit.

    Leitura não recebe commit: num pool isso custa um round-trip e mantém a
    transação aberta mais tempo do que o necessário.
    """
    connection = get_connection()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction() -> Iterator[Any]:
    """Conexão para escrita: commit no sucesso, rollback no erro, close sempre.

    Esta é a resposta ao I-01. O padrão anterior — `connection.close()` como
    última linha da função — devolve a conexão **apenas no caminho feliz**.
    Qualquer coisa que lance antes dela (`cursor()`, `execute()`, `fetchall()`,
    `commit()`, conversão de linha, `int()` sobre coluna nula) pula o close.

    No SQLite o coletor de lixo às vezes salva. No pool do PostgreSQL não
    existe salvação: `putconn` só acontece dentro de `close()`. Medido contra
    PostgreSQL real com `max_size=10`, dez escritas inválidas esgotavam o pool
    e a décima primeira dava `PoolTimeout` — a plataforma inteira parava por
    causa de entradas malformadas de um único tenant.

    `BaseException` e não `Exception`: um `KeyboardInterrupt` ou um
    `CancelledError` no meio de uma escrita também precisa devolver a conexão,
    e com a transação desfeita.
    """
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except BaseException:
        _rollback_quietly(connection)
        raise
    finally:
        connection.close()


def _rollback_quietly(connection: Any) -> None:
    """Desfaz a transação antes de devolver, registrando falha do próprio rollback.

    Engolir aqui é deliberado e tem limite: a exceção original precisa chegar
    a quem chamou. Um rollback que falha não pode substituí-la — mas também não
    pode sumir, porque significa que a conexão está voltando suja.
    """
    rollback = getattr(connection, "rollback", None)
    if rollback is None:
        return
    try:
        rollback()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "database.rollback_failed", extra={"error_type": type(exc).__name__}
        )


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
        with read_connection() as connection:
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
        ) as connection, connection.cursor() as cursor:
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
]

# Migrations da plataforma multiempresa vivem em módulo próprio para manter
# este arquivo focado no schema operacional legado.
from app.storage.migrations_automation import (  # noqa: E402
    AUTOMATION_ID_TABLES,
    AUTOMATION_MIGRATIONS,
)
from app.storage.migrations_hardening import (  # noqa: E402
    HARDENING_ID_TABLES,
    HARDENING_MIGRATIONS,
)
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
SQLITE_ID_TABLES.update(PLATFORM_ID_TABLES)
SQLITE_ID_TABLES.update(IDENTITY_ID_TABLES)
SQLITE_ID_TABLES.update(HARDENING_ID_TABLES)
SQLITE_ID_TABLES.update(AUTOMATION_ID_TABLES)


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
    """Aplica as migrations pendentes.

    A conexão vive num `transaction()`: uma migration que falha no meio desfaz
    o que aplicou e **devolve a conexão**. No padrão anterior ela vazava com a
    transação de escrita aberta, o que no SQLite trava o banco inteiro até o
    timeout e no PostgreSQL consome uma conexão do pool para sempre.
    """
    with transaction() as connection:
        _run_migrations(connection.cursor())
