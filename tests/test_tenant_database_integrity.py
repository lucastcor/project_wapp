"""Database-level tenant integrity must survive application mistakes."""

import sqlite3
from uuid import uuid4

import pytest

from app.storage.db import db_session, get_database_backend
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


@pytest.mark.skipif(
    get_database_backend() != "sqlite",
    reason="SQLite-specific connection pragma.",
)
def test_sqlite_foreign_keys_are_enabled_on_every_connection() -> None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys")
        enabled = cursor.fetchone()[0]

    assert enabled == 1


def test_database_rejects_orphan_company_reference() -> None:
    suffix = uuid4().hex[:12]
    with pytest.raises(Exception) as exc_info, db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO finetuning_jobs (
                job_id,
                company_id,
                status
            )
            VALUES (?, ?, ?)
            """,
            (f"ftjob-orphan-{suffix}", 999_999_999, "pending"),
        )
        connection.commit()

    assert (
        isinstance(exc_info.value, sqlite3.IntegrityError)
        or "foreign key" in str(exc_info.value).lower()
        or "tenant company" in str(exc_info.value).lower()
    )


def test_database_rejects_cross_tenant_reference() -> None:
    suffix = uuid4().hex[:12]
    company_a = repository.create_company(
        slug=f"integrity-a-{suffix}",
        name="Integrity A",
    )
    company_b = repository.create_company(
        slug=f"integrity-b-{suffix}",
        name="Integrity B",
    )
    contact_id = f"5511{suffix}"
    repository.save_contact(
        contact_id=contact_id,
        contact_name="Tenant Reference",
        company_id=company_a,
    )
    pending_question_id = repository.create_pending_question(
        contact_id=contact_id,
        customer_message="Question",
        question_type="other",
        question_for_owner="Resolve",
        company_id=company_a,
    )

    with pytest.raises(Exception) as exc_info, db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO outbound_messages (
                contact_id,
                pending_question_id,
                message,
                source,
                company_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                contact_id,
                pending_question_id,
                "Must not cross tenants",
                "integrity-test",
                company_b,
            ),
        )
        connection.commit()

    assert (
        isinstance(exc_info.value, sqlite3.IntegrityError)
        or "foreign key" in str(exc_info.value).lower()
        or "cross-tenant" in str(exc_info.value).lower()
    )


def _tables_with_company_id(connection) -> set[str]:
    """Tabelas que carregam `company_id`, direto do esquema em uso."""
    cursor = connection.cursor()
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            SELECT c.relname AS name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'company_id'
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0
            """
        )
        return {dict(row)["name"] for row in cursor.fetchall()}

    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {dict(row)["name"] for row in cursor.fetchall()}
    found = set()
    for name in names:
        cursor.execute(f"PRAGMA table_info({name})")
        if any(dict(row)["name"] == "company_id" for row in cursor.fetchall()):
            found.add(name)
    return found


def _tables_with_company_guard(connection) -> set[str]:
    """Tabelas cuja referência a empresa é garantida pelo banco."""
    cursor = connection.cursor()
    if get_database_backend() == "postgres":
        cursor.execute(
            """
            SELECT DISTINCT c.relname AS name
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
                               AND a.attname = 'company_id'
                               AND a.attnum = ANY(k.conkey)
            WHERE n.nspname = 'public' AND k.contype = 'f'
            """
        )
        return {dict(row)["name"] for row in cursor.fetchall()}

    # No SQLite a garantia vem de FK inline **ou** de trigger.
    guarded: set[str] = set()
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    for row in cursor.fetchall():
        name = dict(row)["name"]
        cursor.execute(f"PRAGMA foreign_key_list({name})")
        if any(dict(fk)["from"] == "company_id" for fk in cursor.fetchall()):
            guarded.add(name)
    cursor.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%_company_%'"
    )
    guarded.update(dict(row)["tbl_name"] for row in cursor.fetchall())
    return guarded


def test_every_tenant_scoped_table_has_a_database_level_company_guard() -> None:
    """Nenhuma tabela com `company_id` fica sem garantia no banco.

    Filtrar por `company_id` na query protege a leitura; não protege a
    escrita. Sem a guarda, uma linha pode apontar para uma empresa que não
    existe — e uma linha órfã não aparece em nenhuma listagem tenant-scoped,
    então o defeito só é descoberto por auditoria de contagem.

    A guarda tem de ser do banco porque há vários caminhos de escrita: o
    repositório, os serviços, o restore de backup e a ferramenta de migração
    SQLite->PostgreSQL. Depender de todos lembrarem de validar é o modo de
    falha que este teste existe para impedir.
    """
    with db_session() as connection:
        tenant_tables = _tables_with_company_id(connection)
        guarded = _tables_with_company_guard(connection)

    # `companies` é a própria tabela referenciada.
    unguarded = sorted(tenant_tables - guarded - {"companies"})

    assert unguarded == [], (
        "tabelas com company_id e sem garantia no banco: " + ", ".join(unguarded)
    )


def test_the_guard_audit_actually_sees_the_schema() -> None:
    """Guarda contra o teste acima passar por não enxergar nada."""
    with db_session() as connection:
        tenant_tables = _tables_with_company_id(connection)
    assert len(tenant_tables) >= 40, f"apenas {len(tenant_tables)} tabelas tenant-scoped vistas"
