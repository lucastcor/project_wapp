"""Migration 070 - capacidade de agenda: recurso reservável e guarda de sobreposição.

Contrato (docs/piloto/CONTRATOS.md, "Domínio mínimo" e "Recurso default"):
toda empresa com agenda tem o recurso `default` de capacidade 1, e
agendamentos que ocupam capacidade não se sobrepõem no mesmo
`(empresa, recurso)`, com intervalos `[início, fim)`.

A garantia é do banco, não da aplicação: conversa, API administrativa, fila de
encaixe, restore e ferramenta SQLite->PostgreSQL gravam em
`appointment_bookings`, e a checagem prévia de disponibilidade no serviço não
fecha a corrida entre duas confirmações simultâneas.

- PostgreSQL: constraint de exclusão GiST (extensão `btree_gist`, que precisa
  existir no servidor e ser criável pelo usuário da migration; é "trusted"
  desde o PostgreSQL 13, então basta privilégio CREATE no banco) sobre empresa,
  recurso e o multirange do intervalo do agendamento com o intervalo retido
  durante uma remarcação em andamento.
- SQLite (desenvolvimento): triggers equivalentes. Datas são comparadas por
  `julianday`, que normaliza offsets (`-03:00` e `+00:00` do mesmo instante
  empatam); resolução de milissegundo.

Ocupam capacidade os status `confirmed` e `pending` — os mesmos que a
disponibilidade local, o cancelamento e a remarcação já tratam como ativos.

Sobreposição legada não é resolvida pela migration: ela falha antes de qualquer
DDL com contagem por empresa (sem PII) e nada é apagado, cancelado ou
reatribuído. O relatório com os IDs dos pares roda contra o banco ainda não
migrado: `python -m app.storage.migrations_booking_capacity`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from app.storage.schema_utils import create_table, ensure_column, is_postgres

Migration = tuple[str, str, Callable[[Any], None]]

DEFAULT_RESOURCE_KEY = "default"
DEFAULT_RESOURCE_LABEL = "Agenda principal"
RESOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CAPACITY_STATUSES: tuple[str, ...] = ("confirmed", "pending")
OVERLAP_CONSTRAINT = "ex_appointment_bookings_resource_overlap"
OVERLAP_TRIGGER_MESSAGE = "booking_resource_overlap"
EXCLUSION_VIOLATION_SQLSTATE = "23P01"
BOOKING_CAPACITY_ID_TABLES = {"booking_resources"}
REPORT_COMMAND = "python -m app.storage.migrations_booking_capacity"

# Literal SQL da mesma lista acima; conferido na importação para não divergir.
_CAPACITY_STATUS_SQL = "('confirmed', 'pending')"
_rendered_statuses = "(" + ", ".join(f"'{status}'" for status in CAPACITY_STATUSES) + ")"
if _rendered_statuses != _CAPACITY_STATUS_SQL:
    raise RuntimeError("_CAPACITY_STATUS_SQL diverges from CAPACITY_STATUSES")


class BookingCapacityConflictError(RuntimeError):
    """O banco recusou a escrita: o intervalo já está ocupado no mesmo recurso."""


class BookingResourceUnavailableError(BookingCapacityConflictError):
    """O recurso não existe para a empresa ou está inativo."""


def is_booking_capacity_violation(error: BaseException) -> bool:
    """Se o erro é exatamente a guarda de sobreposição (e não outra integridade)."""
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate is not None:
        if sqlstate != EXCLUSION_VIOLATION_SQLSTATE:
            return False
        diagnostics = getattr(error, "diag", None)
        return getattr(diagnostics, "constraint_name", None) == OVERLAP_CONSTRAINT
    return isinstance(error, sqlite3.IntegrityError) and OVERLAP_TRIGGER_MESSAGE in str(error)


# ── detecção (também usada pelo relatório) ─────────────────────────────

_POSTGRES_PAIR_OVERLAP = (
    "earlier.starts_at < later.ends_at AND later.starts_at < earlier.ends_at"
)
_SQLITE_PAIR_OVERLAP = (
    "julianday(earlier.starts_at) < julianday(later.ends_at) "
    "AND julianday(later.starts_at) < julianday(earlier.ends_at)"
)
_SAME_RESOURCE = "AND later.resource_id = earlier.resource_id"
_RESOURCE_COLUMN = "earlier.resource_id AS resource_id"
_NO_RESOURCE_COLUMN = "NULL AS resource_id"
_POSTGRES_INVALID_INTERVAL = "ends_at <= starts_at"
_SQLITE_INVALID_INTERVAL = (
    "julianday(starts_at) IS NULL OR julianday(ends_at) IS NULL "
    "OR julianday(ends_at) <= julianday(starts_at)"
)


def _has_resource_column(cursor: Any) -> bool:
    if is_postgres():
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'appointment_bookings'
              AND column_name = 'resource_id'
            """
        )
        return cursor.fetchone() is not None
    cursor.execute("PRAGMA table_info(appointment_bookings)")
    return any(dict(row)["name"] == "resource_id" for row in cursor.fetchall())


def list_capacity_overlaps(cursor: Any, *, resource_aware: bool | None = None) -> list[dict[str, Any]]:
    """Pares de agendamentos ativos da mesma empresa (e recurso) que se sobrepõem.

    Antes da 070 não existe coluna de recurso: toda reserva de empresa vai para
    o recurso `default`, então o par é por empresa. Só IDs e horários — nada de
    contato, título ou descrição.
    """
    aware = _has_resource_column(cursor) if resource_aware is None else resource_aware
    overlap = _POSTGRES_PAIR_OVERLAP if is_postgres() else _SQLITE_PAIR_OVERLAP
    same_resource = _SAME_RESOURCE if aware else ""
    resource_column = _RESOURCE_COLUMN if aware else _NO_RESOURCE_COLUMN
    cursor.execute(
        f"""
        SELECT
            earlier.company_id AS company_id,
            {resource_column},
            earlier.id AS booking_id,
            later.id AS conflicting_booking_id,
            earlier.starts_at AS starts_at,
            earlier.ends_at AS ends_at,
            later.starts_at AS conflicting_starts_at,
            later.ends_at AS conflicting_ends_at
        FROM appointment_bookings AS earlier
        JOIN appointment_bookings AS later
          ON later.company_id = earlier.company_id
         AND later.id > earlier.id
         {same_resource}
         AND {overlap}
        WHERE earlier.company_id IS NOT NULL
          AND earlier.status IN {_CAPACITY_STATUS_SQL}
          AND later.status IN {_CAPACITY_STATUS_SQL}
        ORDER BY earlier.company_id, earlier.id, later.id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def list_invalid_intervals(cursor: Any) -> list[dict[str, Any]]:
    """Agendamentos ativos de empresa cujo fim não é posterior ao início."""
    invalid = _POSTGRES_INVALID_INTERVAL if is_postgres() else _SQLITE_INVALID_INTERVAL
    cursor.execute(
        f"""
        SELECT company_id, id AS booking_id
        FROM appointment_bookings
        WHERE company_id IS NOT NULL
          AND status IN {_CAPACITY_STATUS_SQL}
          AND ({invalid})
        ORDER BY company_id, id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def summarize_capacity_issues(
    overlaps: list[dict[str, Any]],
    invalid_intervals: list[dict[str, Any]],
) -> list[str]:
    """Linhas de resumo por empresa/recurso: só identificadores e contagens."""
    pairs: dict[tuple[int, Any], int] = {}
    bookings: dict[tuple[int, Any], set[int]] = {}
    for pair in overlaps:
        key = (int(pair["company_id"]), pair.get("resource_id"))
        pairs[key] = pairs.get(key, 0) + 1
        bookings.setdefault(key, set()).update(
            {int(pair["booking_id"]), int(pair["conflicting_booking_id"])}
        )
    lines = [
        f"company_id={company_id} resource={resource_id if resource_id is not None else DEFAULT_RESOURCE_KEY}: "
        f"{len(bookings[(company_id, resource_id)])} booking(s) in {count} overlapping pair(s)"
        for (company_id, resource_id), count in sorted(pairs.items(), key=lambda item: (item[0][0], str(item[0][1])))
    ]
    invalid_by_company: dict[int, int] = {}
    for row in invalid_intervals:
        invalid_by_company[int(row["company_id"])] = invalid_by_company.get(int(row["company_id"]), 0) + 1
    lines.extend(
        f"company_id={company_id}: {count} active booking(s) with ends_at <= starts_at"
        for company_id, count in sorted(invalid_by_company.items())
    )
    return lines


def _assert_no_legacy_conflicts(cursor: Any) -> None:
    overlaps = list_capacity_overlaps(cursor, resource_aware=False)
    invalid_intervals = list_invalid_intervals(cursor)
    if not overlaps and not invalid_intervals:
        return
    summary = "; ".join(summarize_capacity_issues(overlaps, invalid_intervals))
    raise RuntimeError(
        "Booking capacity migration 070 blocked: active bookings (status confirmed/pending) "
        "must not overlap on the same resource and must end after they start before the "
        f"database guard is enabled. {summary}. No booking was changed. List the booking ids "
        f"with `{REPORT_COMMAND}`, cancel or reschedule one booking of each pair through the "
        "admin API, then restart the upgrade."
    )


# ── DDL ────────────────────────────────────────────────────────────────


def _create_resources_table(cursor: Any) -> None:
    create_table(cursor, "booking_resources", """
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            resource_key TEXT NOT NULL,
            label TEXT,
            capacity INTEGER NOT NULL DEFAULT 1 CHECK (capacity = 1),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_booking_resources_company_key UNIQUE (company_id, resource_key),
            CONSTRAINT uq_booking_resources_id_company UNIQUE (id, company_id)
    """)


def _assign_default_resources(cursor: Any) -> None:
    # Empresa com agenda = empresa com agendamento ou integração de calendário.
    cursor.execute(
        """
        INSERT INTO booking_resources (company_id, resource_key, label, capacity, active)
        SELECT company.id, 'default', 'Agenda principal', 1, 1
        FROM companies AS company
        WHERE (
                EXISTS (SELECT 1 FROM appointment_bookings AS booking WHERE booking.company_id = company.id)
             OR EXISTS (SELECT 1 FROM calendar_integrations AS integration WHERE integration.company_id = company.id)
              )
          AND NOT EXISTS (
                SELECT 1 FROM booking_resources AS resource
                WHERE resource.company_id = company.id AND resource.resource_key = 'default'
              )
        ORDER BY company.id
        """
    )
    # Determinístico: todo agendamento de empresa, em qualquer status, vai para
    # o recurso default da própria empresa. Sem empresa, sem recurso.
    cursor.execute(
        """
        UPDATE appointment_bookings
        SET resource_id = (
            SELECT resource.id
            FROM booking_resources AS resource
            WHERE resource.company_id = appointment_bookings.company_id
              AND resource.resource_key = 'default'
        )
        WHERE company_id IS NOT NULL
          AND resource_id IS NULL
        """
    )


_POSTGRES_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    (
        "fk_appointment_bookings_resource_tenant",
        "FOREIGN KEY (resource_id, company_id) REFERENCES booking_resources (id, company_id) "
        "ON DELETE RESTRICT NOT VALID",
    ),
    (
        # A FK composta é MATCH SIMPLE: com company_id nulo ela não confere nada.
        "ck_appointment_bookings_resource_scope",
        "CHECK ((company_id IS NULL AND resource_id IS NULL) "
        "OR (company_id IS NOT NULL AND resource_id IS NOT NULL)) NOT VALID",
    ),
    (
        "ck_appointment_bookings_hold_interval",
        "CHECK ((hold_starts_at IS NULL AND hold_ends_at IS NULL) "
        "OR (hold_starts_at IS NOT NULL AND hold_ends_at IS NOT NULL AND hold_ends_at > hold_starts_at)) "
        "NOT VALID",
    ),
    (
        "ck_appointment_bookings_active_interval",
        "CHECK (status NOT IN ('confirmed', 'pending') OR ends_at > starts_at) NOT VALID",
    ),
)

# Intervalo ocupado = intervalo do agendamento mais o retido durante remarcação.
_POSTGRES_OVERLAP_EXCLUSION = """
    EXCLUDE USING gist (
        company_id WITH =,
        resource_id WITH =,
        (tstzmultirange(
            tstzrange(starts_at, ends_at, '[)'),
            tstzrange(COALESCE(hold_starts_at, starts_at), COALESCE(hold_ends_at, ends_at), '[)')
        )) WITH &&
    ) WHERE (status IN ('confirmed', 'pending'))
"""


def _postgres_constraint_exists(cursor: Any, constraint: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conname = ?
          AND conrelid = 'appointment_bookings'::regclass
        """,
        (constraint,),
    )
    return cursor.fetchone() is not None


def _require_timestamptz_columns(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'appointment_bookings'
          AND column_name IN ('starts_at', 'ends_at', 'hold_starts_at', 'hold_ends_at')
        """
    )
    wrong = sorted(
        str(dict(row)["column_name"])
        for row in cursor.fetchall()
        if dict(row)["data_type"] != "timestamp with time zone"
    )
    if wrong:
        raise RuntimeError(
            "Booking capacity migration 070 blocked: appointment_bookings columns "
            f"{', '.join(wrong)} must be TIMESTAMPTZ for the overlap guard."
        )


def _ensure_btree_gist(cursor: Any) -> None:
    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
    if cursor.fetchone() is not None:
        return
    cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'btree_gist'")
    if cursor.fetchone() is None:
        raise RuntimeError(
            "Booking capacity migration 070 requires the PostgreSQL extension btree_gist "
            "(contrib), which this server does not provide. Install the contrib package "
            "or use an image that ships it (the official postgres images do)."
        )
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    except Exception as exc:
        raise RuntimeError(
            "Booking capacity migration 070 could not create the btree_gist extension. "
            "The migration role needs CREATE on the database (btree_gist is a trusted "
            "extension), or a superuser must run `CREATE EXTENSION btree_gist` first."
        ) from exc


def _guard_postgres(cursor: Any) -> None:
    _require_timestamptz_columns(cursor)
    _ensure_btree_gist(cursor)
    for constraint, definition in _POSTGRES_CONSTRAINTS:
        if _postgres_constraint_exists(cursor, constraint):
            continue
        cursor.execute(f"ALTER TABLE appointment_bookings ADD CONSTRAINT {constraint} {definition}")
        cursor.execute(f"ALTER TABLE appointment_bookings VALIDATE CONSTRAINT {constraint}")
    if not _postgres_constraint_exists(cursor, OVERLAP_CONSTRAINT):
        cursor.execute(
            f"ALTER TABLE appointment_bookings ADD CONSTRAINT {OVERLAP_CONSTRAINT} {_POSTGRES_OVERLAP_EXCLUSION}"
        )


# Conflito entre o intervalo ocupado de NEW e o de outra linha ativa. Com hold
# nulo, `julianday(NULL)` é nulo e a comparação correspondente é falsa.
_SQLITE_OVERLAP_WITH_NEW = """
    (julianday(other.starts_at) < julianday(NEW.ends_at) AND julianday(other.ends_at) > julianday(NEW.starts_at))
    OR (julianday(other.starts_at) < julianday(NEW.hold_ends_at) AND julianday(other.ends_at) > julianday(NEW.hold_starts_at))
    OR (julianday(other.hold_starts_at) < julianday(NEW.ends_at) AND julianday(other.hold_ends_at) > julianday(NEW.starts_at))
    OR (julianday(other.hold_starts_at) < julianday(NEW.hold_ends_at) AND julianday(other.hold_ends_at) > julianday(NEW.hold_starts_at))
"""
_SQLITE_INVALID_NEW_INTERVAL = """
    julianday(NEW.starts_at) IS NULL
    OR julianday(NEW.ends_at) IS NULL
    OR julianday(NEW.ends_at) <= julianday(NEW.starts_at)
    OR (NEW.hold_starts_at IS NULL) <> (NEW.hold_ends_at IS NULL)
    OR (NEW.hold_starts_at IS NOT NULL AND (
        julianday(NEW.hold_starts_at) IS NULL
        OR julianday(NEW.hold_ends_at) IS NULL
        OR julianday(NEW.hold_ends_at) <= julianday(NEW.hold_starts_at)
    ))
"""
_SQLITE_RESOURCE_SCOPE_VIOLATION = """
    (NEW.company_id IS NULL AND NEW.resource_id IS NOT NULL)
    OR (NEW.company_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM booking_resources
        WHERE id = NEW.resource_id AND company_id = NEW.company_id
    ))
"""

_SQLITE_TRIGGERS: tuple[tuple[str, str], ...] = (
    (
        "trg_appointment_bookings_resource_scope_insert",
        f"""
        CREATE TRIGGER trg_appointment_bookings_resource_scope_insert
        BEFORE INSERT ON appointment_bookings
        FOR EACH ROW
        WHEN {_SQLITE_RESOURCE_SCOPE_VIOLATION}
        BEGIN
            SELECT RAISE(ABORT, 'booking resource must belong to the booking company');
        END
        """,
    ),
    (
        "trg_appointment_bookings_resource_scope_update",
        f"""
        CREATE TRIGGER trg_appointment_bookings_resource_scope_update
        BEFORE UPDATE OF company_id, resource_id ON appointment_bookings
        FOR EACH ROW
        WHEN {_SQLITE_RESOURCE_SCOPE_VIOLATION}
        BEGIN
            SELECT RAISE(ABORT, 'booking resource must belong to the booking company');
        END
        """,
    ),
    (
        "trg_appointment_bookings_capacity_insert",
        f"""
        CREATE TRIGGER trg_appointment_bookings_capacity_insert
        BEFORE INSERT ON appointment_bookings
        FOR EACH ROW
        WHEN NEW.company_id IS NOT NULL AND NEW.status IN {_CAPACITY_STATUS_SQL}
        BEGIN
            SELECT RAISE(ABORT, 'booking interval is invalid')
            WHERE {_SQLITE_INVALID_NEW_INTERVAL};
            SELECT RAISE(ABORT, 'booking_resource_overlap')
            WHERE EXISTS (
                SELECT 1 FROM appointment_bookings AS other
                WHERE other.company_id = NEW.company_id
                  AND other.resource_id = NEW.resource_id
                  AND other.status IN {_CAPACITY_STATUS_SQL}
                  AND ({_SQLITE_OVERLAP_WITH_NEW})
            );
        END
        """,
    ),
    (
        "trg_appointment_bookings_capacity_update",
        f"""
        CREATE TRIGGER trg_appointment_bookings_capacity_update
        BEFORE UPDATE OF company_id, resource_id, status, starts_at, ends_at, hold_starts_at, hold_ends_at
        ON appointment_bookings
        FOR EACH ROW
        WHEN NEW.company_id IS NOT NULL AND NEW.status IN {_CAPACITY_STATUS_SQL}
        BEGIN
            SELECT RAISE(ABORT, 'booking interval is invalid')
            WHERE {_SQLITE_INVALID_NEW_INTERVAL};
            SELECT RAISE(ABORT, 'booking_resource_overlap')
            WHERE EXISTS (
                SELECT 1 FROM appointment_bookings AS other
                WHERE other.company_id = NEW.company_id
                  AND other.resource_id = NEW.resource_id
                  AND other.id <> NEW.id
                  AND other.status IN {_CAPACITY_STATUS_SQL}
                  AND ({_SQLITE_OVERLAP_WITH_NEW})
            );
        END
        """,
    ),
    (
        # Equivalente ao ON DELETE RESTRICT da FK composta no PostgreSQL.
        "trg_booking_resources_in_use_delete",
        """
        CREATE TRIGGER trg_booking_resources_in_use_delete
        BEFORE DELETE ON booking_resources
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM appointment_bookings WHERE resource_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'booking resource is referenced by bookings');
        END
        """,
    ),
    (
        "trg_booking_resources_in_use_update",
        """
        CREATE TRIGGER trg_booking_resources_in_use_update
        BEFORE UPDATE OF id, company_id ON booking_resources
        FOR EACH ROW
        WHEN (NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id)
          AND EXISTS (SELECT 1 FROM appointment_bookings WHERE resource_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'booking resource is referenced by bookings');
        END
        """,
    ),
)


def _guard_sqlite(cursor: Any) -> None:
    for trigger, statement in _SQLITE_TRIGGERS:
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        cursor.execute(statement)


def _create_booking_capacity(cursor: Any) -> None:
    # Antes de qualquer DDL: com conflito legado nada muda no banco.
    _assert_no_legacy_conflicts(cursor)

    _create_resources_table(cursor)
    ensure_column(cursor, "appointment_bookings", "resource_id", "INTEGER")
    ensure_column(cursor, "appointment_bookings", "hold_starts_at", "TIMESTAMP")
    ensure_column(cursor, "appointment_bookings", "hold_ends_at", "TIMESTAMP")
    _assign_default_resources(cursor)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_appointment_bookings_resource_starts_at "
        "ON appointment_bookings (company_id, resource_id, starts_at)"
    )

    if is_postgres():
        _guard_postgres(cursor)
    else:
        _guard_sqlite(cursor)


BOOKING_CAPACITY_MIGRATIONS: list[Migration] = [
    (
        "070_booking_capacity",
        "Bookable resources and database guard against overlapping bookings",
        _create_booking_capacity,
    ),
]


# ── relatório para o operador ──────────────────────────────────────────


def build_capacity_report() -> dict[str, Any]:
    """Pares sobrepostos e intervalos inválidos, sem migrar o banco.

    Não chama `init_db()`: precisa rodar exatamente no banco cuja migração foi
    bloqueada. Não inclui contato, título nem descrição.
    """
    from app.storage.db import db_session, get_database_backend

    with db_session() as connection:
        cursor = connection.cursor()
        overlaps = list_capacity_overlaps(cursor)
        invalid_intervals = list_invalid_intervals(cursor)
    return {
        "database_backend": get_database_backend(),
        "capacity_statuses": list(CAPACITY_STATUSES),
        "summary": summarize_capacity_issues(overlaps, invalid_intervals),
        "overlapping_pairs": overlaps,
        "invalid_intervals": invalid_intervals,
    }


def main() -> int:
    report = build_capacity_report()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report["overlapping_pairs"] or report["invalid_intervals"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
