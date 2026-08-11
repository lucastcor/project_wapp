"""End-to-end SQLite -> PostgreSQL migration against a real PostgreSQL server.

Requires ``TEST_POSTGRES_MIGRATION_URL`` (or ``TEST_DATABASE_URL`` pointing at
PostgreSQL). PostgreSQL is never mocked here: the point is to prove sequences,
foreign keys and types survive a real migration.
"""

import copy
import json
import os
import sqlite3
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest

from app.core.config import settings
from app.tools.migrate_sqlite_to_postgres import (
    MIGRATION_TABLE_NAMES,
    TABLE_COLUMNS,
    audit_migration_coverage,
    migrate,
)


def _postgres_base_url() -> str | None:
    for variable in ("TEST_POSTGRES_MIGRATION_URL", "TEST_DATABASE_URL"):
        value = os.environ.get(variable, "").strip()
        if value.startswith(("postgres://", "postgresql://")):
            return value
    return None


POSTGRES_BASE_URL = _postgres_base_url()
requires_postgres = pytest.mark.skipif(
    POSTGRES_BASE_URL is None,
    reason="Set TEST_POSTGRES_MIGRATION_URL or TEST_DATABASE_URL to a PostgreSQL URL.",
)
PERSISTED_CLIENT_SECRET = "MIGRATION_CLIENT_SECRET_SENTINEL"
PERSISTED_REFRESH_TOKEN = "MIGRATION_REFRESH_TOKEN_SENTINEL"


def _target_url(database_name: str) -> str:
    parsed = urlparse(POSTGRES_BASE_URL)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


@pytest.fixture
def postgres_target():
    """Create an isolated target database and drop it afterwards."""
    import psycopg

    database_name = f"wapp_mig_{uuid4().hex[:12]}"
    admin_url = _target_url("postgres")

    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{database_name}"')
    try:
        yield _target_url(database_name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


@pytest.fixture
def sqlite_source(tmp_path, monkeypatch):
    """A populated SQLite installation with two tenants."""
    source_path = tmp_path / "source.db"

    original_url = settings.database_url
    original_pg_url = settings.postgres_database_url
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{source_path}")

    from app.core.company_context import get_company_context, save_company_context
    from app.storage.db import init_db
    from app.storage.repository import RuntimeRepository

    init_db()
    repository = RuntimeRepository()
    base_context = copy.deepcopy(get_company_context())

    suffix = uuid4().hex[:8]
    company_a = repository.create_company(slug=f"mig-a-{suffix}", name="Mig Tenant A")
    company_b = repository.create_company(slug=f"mig-b-{suffix}", name="Mig Tenant B")

    for slug, label in [(f"mig-a-{suffix}", "Context A"), (f"mig-b-{suffix}", "Context B")]:
        payload = copy.deepcopy(base_context)
        payload["company_name"] = label
        save_company_context(payload, company_slug=slug, updated_by="migration-fixture")

    shared_contact = f"mig-contact-{suffix}"
    repository.save_contact(shared_contact, "Alice A", company_id=company_a)
    repository.save_contact(shared_contact, "Alice B", company_id=company_b)
    repository.update_contact(
        shared_contact, contact_name="Alice A", company_id=company_a, customer_profile="vip"
    )
    repository.save_message(
        contact_id=shared_contact,
        company_id=company_a,
        message="mensagem migrada",
        classification="faq",
        reply="ok",
    )
    repository.create_pending_question(
        contact_id=shared_contact,
        company_id=company_b,
        customer_message="pergunta migrada",
        question_type="pricing",
        question_for_owner="responder",
    )

    active_phone = f"mig-active-{suffix}"
    inactive_phone = f"mig-inactive-{suffix}"
    repository.upsert_whatsapp_binding(
        company_id=company_a,
        phone_number_id=active_phone,
        display_phone_number="5511911112222",
        updated_by="migration-fixture",
    )
    historical = repository.upsert_whatsapp_binding(
        company_id=company_b,
        phone_number_id=inactive_phone,
        updated_by="migration-fixture",
    )
    repository.set_whatsapp_binding_active(int(historical["id"]), active=False)

    integration_id = repository.create_calendar_integration(
        provider="google_calendar",
        company_id=company_a,
        external_calendar_id="primary",
        config={
            "calendar_id": "primary",
            "client_id": "public-client-id",
            "client_secret": PERSISTED_CLIENT_SECRET,
            "nested": {
                "refresh_token": PERSISTED_REFRESH_TOKEN,
                "phone_number_id": "public-phone-number-id",
            },
        },
        active=True,
    )
    booking_id = repository.create_appointment_booking(
        provider="google_calendar",
        title="Migration appointment",
        starts_at="2026-07-26T10:00:00+00:00",
        ends_at="2026-07-26T11:00:00+00:00",
        timezone="UTC",
        company_id=company_a,
        contact_id=shared_contact,
        integration_id=integration_id,
        scheduling_profile_key="retorno",
        customer_profile="returning_customer",
        status="cancelled",
    )
    repository.update_appointment_booking_outcome(
        booking_id,
        outcome="no_show",
        notes="migration outcome note",
        company_id=company_a,
    )
    repository.update_appointment_booking_cancellation_reason(
        booking_id,
        "customer request",
        company_id=company_a,
    )

    context = {
        "path": str(source_path),
        "company_a": company_a,
        "company_b": company_b,
        "slug_a": f"mig-a-{suffix}",
        "slug_b": f"mig-b-{suffix}",
        "contact_id": shared_contact,
        "active_phone": active_phone,
        "inactive_phone": inactive_phone,
        "integration_id": integration_id,
        "booking_id": booking_id,
    }

    yield context

    settings.database_url = original_url
    settings.postgres_database_url = original_pg_url


def _rows(url: str, query: str, params: tuple = ()) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]


# ── Coverage guard (no PostgreSQL needed) ─────────────────────────────


def test_migration_covers_every_runtime_table() -> None:
    assert audit_migration_coverage() == []


def test_tenant_configuration_tables_are_migrated() -> None:
    assert "company_contexts" in MIGRATION_TABLE_NAMES
    assert "whatsapp_channel_bindings" in MIGRATION_TABLE_NAMES
    assert "contact_company_profiles" in MIGRATION_TABLE_NAMES


def test_appointment_booking_manifest_contains_every_operational_column() -> None:
    manifest = {table: columns for table, columns, _identity in TABLE_COLUMNS}

    assert {
        "scheduling_profile_key",
        "customer_profile",
        "booking_outcome",
        "booking_outcome_at",
        "booking_outcome_notes",
        "cancellation_reason",
    }.issubset(manifest["appointment_bookings"])


def test_migration_order_is_foreign_key_safe() -> None:
    order = {table: index for index, table in enumerate(MIGRATION_TABLE_NAMES)}

    assert order["companies"] < order["company_contexts"]
    assert order["companies"] < order["whatsapp_channel_bindings"]
    assert order["contacts"] < order["contact_company_profiles"]
    assert order["contacts"] < order["messages"]


def test_unmapped_source_table_fails_before_connecting_to_postgres(
    monkeypatch,
    tmp_path,
) -> None:
    from app.storage.db import init_db

    source = tmp_path / "unexpected-table.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{source}")
    init_db()
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE unexpected_runtime_data (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="unmapped table: unexpected_runtime_data"):
        migrate(
            sqlite_path=str(source),
            postgres_url="postgresql://unused/unused",
        )


def test_unmapped_source_column_fails_before_connecting_to_postgres(
    monkeypatch,
    tmp_path,
) -> None:
    from app.storage.db import init_db

    source = tmp_path / "unexpected-column.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{source}")
    init_db()
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE appointment_bookings ADD COLUMN future_value TEXT")

    with pytest.raises(RuntimeError, match="unmapped column: appointment_bookings.future_value"):
        migrate(
            sqlite_path=str(source),
            postgres_url="postgresql://unused/unused",
        )


# ── Real end-to-end migration ─────────────────────────────────────────


@requires_postgres
def test_migration_preserves_tenant_configuration(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    contexts = _rows(
        postgres_target,
        "SELECT company_id, company_slug, context_json, revision FROM company_contexts ORDER BY id",
    )
    slugs = {row["company_slug"] for row in contexts}
    assert sqlite_source["slug_a"] in slugs
    assert sqlite_source["slug_b"] in slugs
    for row in contexts:
        if row["company_slug"] == sqlite_source["slug_a"]:
            assert "Context A" in row["context_json"]
            assert row["company_id"] == sqlite_source["company_a"]

    bindings = _rows(
        postgres_target,
        "SELECT company_id, phone_number_id, active, display_phone_number FROM whatsapp_channel_bindings ORDER BY id",
    )
    by_phone = {row["phone_number_id"]: row for row in bindings}
    assert by_phone[sqlite_source["active_phone"]]["active"] == 1
    assert by_phone[sqlite_source["active_phone"]]["company_id"] == sqlite_source["company_a"]
    assert by_phone[sqlite_source["active_phone"]]["display_phone_number"] == "5511911112222"
    assert by_phone[sqlite_source["inactive_phone"]]["active"] == 0
    assert by_phone[sqlite_source["inactive_phone"]]["company_id"] == sqlite_source["company_b"]


@requires_postgres
def test_migration_preserves_contact_profiles_and_operational_data(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    profiles = _rows(
        postgres_target,
        "SELECT contact_id, company_id, customer_profile FROM contact_company_profiles"
        " WHERE contact_id = %s ORDER BY company_id",
        (sqlite_source["contact_id"],),
    )
    assert len(profiles) == 2
    company_ids = {row["company_id"] for row in profiles}
    assert company_ids == {sqlite_source["company_a"], sqlite_source["company_b"]}
    vip = [row for row in profiles if row["company_id"] == sqlite_source["company_a"]]
    assert vip[0]["customer_profile"] == "vip"

    messages = _rows(postgres_target, "SELECT company_id, message FROM messages ORDER BY id")
    assert any(row["message"] == "mensagem migrada" for row in messages)

    questions = _rows(postgres_target, "SELECT company_id, customer_message FROM pending_questions")
    assert any(row["customer_message"] == "pergunta migrada" for row in questions)


@requires_postgres
def test_migration_preserves_all_appointment_operational_fields(
    sqlite_source,
    postgres_target,
) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    rows = _rows(
        postgres_target,
        """
        SELECT
            scheduling_profile_key,
            customer_profile,
            booking_outcome,
            booking_outcome_at,
            booking_outcome_notes,
            cancellation_reason
        FROM appointment_bookings
        WHERE id = %s
        """,
        (sqlite_source["booking_id"],),
    )

    assert len(rows) == 1
    assert rows[0]["scheduling_profile_key"] == "retorno"
    assert rows[0]["customer_profile"] == "returning_customer"
    assert rows[0]["booking_outcome"] == "no_show"
    assert rows[0]["booking_outcome_at"] is not None
    assert rows[0]["booking_outcome_notes"] == "migration outcome note"
    assert rows[0]["cancellation_reason"] == "customer request"


@requires_postgres
def test_migration_sanitizes_persisted_calendar_credentials(
    sqlite_source,
    postgres_target,
) -> None:
    result = migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    integrations = _rows(
        postgres_target,
        "SELECT active, config_json FROM calendar_integrations WHERE id = %s",
        (sqlite_source["integration_id"],),
    )
    assert len(integrations) == 1
    serialized = integrations[0]["config_json"]
    assert PERSISTED_CLIENT_SECRET not in serialized
    assert PERSISTED_REFRESH_TOKEN not in serialized
    config = json.loads(serialized)
    assert config["calendar_id"] == "primary"
    assert config["client_id"] == "public-client-id"
    assert config["nested"]["phone_number_id"] == "public-phone-number-id"
    assert config["_credentials_redacted"] is True
    assert integrations[0]["active"] == 0
    assert result["sanitization"]["sensitive_fields_removed"] >= 2
    assert result["sanitization"]["integrations_disabled"] >= 1
    assert sqlite_source["integration_id"] in result["sanitization"][
        "integration_ids_requiring_reconfiguration"
    ]


@requires_postgres
def test_migrated_foreign_keys_are_valid(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    orphans = _rows(
        postgres_target,
        """
        SELECT 'company_contexts' AS source, COUNT(*) AS orphan_count
        FROM company_contexts c
        WHERE c.company_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM companies o WHERE o.id = c.company_id)
        UNION ALL
        SELECT 'whatsapp_channel_bindings', COUNT(*)
        FROM whatsapp_channel_bindings b
        WHERE NOT EXISTS (SELECT 1 FROM companies o WHERE o.id = b.company_id)
        UNION ALL
        SELECT 'contact_company_profiles', COUNT(*)
        FROM contact_company_profiles p
        WHERE NOT EXISTS (SELECT 1 FROM companies o WHERE o.id = p.company_id)
        """,
    )
    assert all(row["orphan_count"] == 0 for row in orphans), orphans


@requires_postgres
def test_sequences_generate_non_conflicting_ids_after_migration(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    import psycopg

    with psycopg.connect(postgres_target, autocommit=True) as connection:
        for table in ["companies", "company_contexts", "whatsapp_channel_bindings", "messages"]:
            max_id = connection.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
            next_id = connection.execute(
                "SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (table,)
            ).fetchone()[0]
            assert next_id > max_id, f"{table} sequence would collide ({next_id} <= {max_id})"


@requires_postgres
def test_active_binding_uniqueness_survives_migration(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    import psycopg

    with psycopg.connect(postgres_target, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO whatsapp_channel_bindings
                    (company_id, phone_number_id, active, created_at, updated_at)
                VALUES (%s, %s, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (sqlite_source["company_b"], sqlite_source["active_phone"]),
            )


@requires_postgres
def test_tenant_isolation_survives_migration(sqlite_source, postgres_target) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    tenant_a_messages = _rows(
        postgres_target,
        "SELECT message FROM messages WHERE company_id = %s",
        (sqlite_source["company_a"],),
    )
    tenant_b_messages = _rows(
        postgres_target,
        "SELECT message FROM messages WHERE company_id = %s",
        (sqlite_source["company_b"],),
    )
    assert any(row["message"] == "mensagem migrada" for row in tenant_a_messages)
    assert all(row["message"] != "mensagem migrada" for row in tenant_b_messages)


@requires_postgres
def test_migrated_database_passes_application_readiness(sqlite_source, postgres_target, monkeypatch) -> None:
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    from app.storage.db import (
        check_runtime_database_connection,
        close_postgres_pool,
        get_database_backend,
    )
    from app.storage.repository import RuntimeRepository

    close_postgres_pool()
    monkeypatch.setattr(settings, "database_url", postgres_target)
    monkeypatch.setattr(settings, "postgres_database_url", postgres_target)
    try:
        assert get_database_backend() == "postgres"
        probe = check_runtime_database_connection()
        assert probe["ok"] is True

        repository = RuntimeRepository()
        # Optional filters and tenant reads must work on the migrated database.
        assert isinstance(repository.get_companies(), list)
        assert isinstance(repository.get_messages(), list)
        assert repository.get_whatsapp_binding_by_phone_number_id(
            sqlite_source["active_phone"]
        )["company_id"] == sqlite_source["company_a"]
        assert len(repository.get_schema_migrations()) > 0
    finally:
        close_postgres_pool()


@requires_postgres
def test_repeated_migration_is_safe(sqlite_source, postgres_target) -> None:
    """The tool's contract is truncate-then-load, so re-running must not duplicate."""
    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)
    first = _rows(postgres_target, "SELECT COUNT(*) AS total FROM whatsapp_channel_bindings")[0]["total"]

    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)
    second = _rows(postgres_target, "SELECT COUNT(*) AS total FROM whatsapp_channel_bindings")[0]["total"]

    assert first == second


@requires_postgres
def test_migration_failure_rolls_back_target_data(
    sqlite_source,
    postgres_target,
    monkeypatch,
) -> None:
    import app.tools.migrate_sqlite_to_postgres as migration_module
    import psycopg

    original_url = settings.database_url
    original_pg_url = settings.postgres_database_url
    from app.storage.db import close_postgres_pool, init_db

    close_postgres_pool()
    settings.database_url = postgres_target
    settings.postgres_database_url = postgres_target
    init_db()
    close_postgres_pool()
    settings.database_url = original_url
    settings.postgres_database_url = original_pg_url

    with psycopg.connect(postgres_target, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO companies (slug, name, active) VALUES (%s, %s, 1)",
            ("rollback-target-sentinel", "Untouched"),
        )

    original_sanitizer = migration_module.sanitize_runtime_row

    def fail_after_companies(table_name, row):
        if table_name == "messages":
            raise RuntimeError("injected migration failure")
        return original_sanitizer(table_name, row)

    monkeypatch.setattr(migration_module, "sanitize_runtime_row", fail_after_companies)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    sentinel = _rows(
        postgres_target,
        "SELECT name FROM companies WHERE slug = %s",
        ("rollback-target-sentinel",),
    )
    assert sentinel == [{"name": "Untouched"}]


@requires_postgres
def test_legacy_source_uses_safe_target_defaults(tmp_path, postgres_target) -> None:
    source = tmp_path / "legacy-defaults.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL,
                name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO companies (id, slug, name) VALUES (1, 'legacy-safe', 'Legacy Safe')"
        )

    migrate(sqlite_path=str(source), postgres_url=postgres_target)

    assert _rows(
        postgres_target,
        "SELECT id, slug, name, active FROM companies WHERE id = 1",
    ) == [{"id": 1, "slug": "legacy-safe", "name": "Legacy Safe", "active": 1}]


@requires_postgres
def test_legacy_source_missing_required_column_fails(
    tmp_path,
    postgres_target,
) -> None:
    source = tmp_path / "legacy-invalid.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO companies (id, name) VALUES (1, 'Missing slug')")

    with pytest.raises(RuntimeError, match="lacks required columns.*slug"):
        migrate(sqlite_path=str(source), postgres_url=postgres_target)


@requires_postgres
def test_migration_does_not_copy_sqlite_internal_tables(sqlite_source, postgres_target) -> None:
    with sqlite3.connect(sqlite_source["path"]) as connection:
        internal = connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'sqlite_%'"
        ).fetchall()

    migrate(sqlite_path=sqlite_source["path"], postgres_url=postgres_target)

    target_tables = {
        row["tablename"]
        for row in _rows(
            postgres_target,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
        )
    }
    for (name,) in internal:
        assert name not in target_tables
