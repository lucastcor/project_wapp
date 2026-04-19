import sqlite3

from app.core.config import settings
from app.storage import db


def test_get_database_backend_for_sqlite(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "sqlite:///project_wapp.db")

    assert db.get_database_backend() == "sqlite"


def test_get_database_backend_for_postgres(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql://project_wapp:secret@localhost:5432/project_wapp",
    )

    assert db.get_database_backend() == "postgres"


def test_normalize_postgres_query_for_contacts_upsert() -> None:
    query, params, returning_table = db._normalize_postgres_query(
        """
        INSERT OR IGNORE INTO contacts (contact_id, contact_name, company_id)
        VALUES (?, ?, ?)
        """,
        ("c1", "Cliente", 1),
    )

    assert "ON CONFLICT (contact_id) DO NOTHING" in query
    assert "%s" in query
    assert params == ("c1", "Cliente", 1)
    assert returning_table is None


def test_normalize_postgres_query_for_contacts_upsert_with_inline_nulls() -> None:
    query, params, returning_table = db._normalize_postgres_query(
        """
        INSERT OR IGNORE INTO contacts (contact_id, contact_name, company_id)
        VALUES (?, NULL, NULL)
        """,
        ("c1",),
    )

    assert "ON CONFLICT (contact_id) DO NOTHING" in query
    assert "%s" in query
    assert params == ("c1",)
    assert returning_table is None


def test_normalize_postgres_query_adds_returning_for_identity_tables() -> None:
    query, params, returning_table = db._normalize_postgres_query(
        """
        INSERT INTO companies (
            slug,
            name,
            business_type,
            active,
            updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        ("empresa-a", "Empresa A", "general", 1),
    )

    assert "RETURNING id" in query
    assert params == ("empresa-a", "Empresa A", "general", 1)
    assert returning_table == "companies"


def test_create_contact_company_profiles_backfills_legacy_contacts_without_optional_columns(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "sqlite:///legacy-test.db")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE contacts (
            contact_id TEXT PRIMARY KEY,
            contact_name TEXT,
            company_id INTEGER
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO contacts (contact_id, contact_name, company_id)
        VALUES ('c1', 'Cliente Legacy', 5)
        """
    )

    db._create_contact_company_profiles(cursor)

    cursor.execute(
        """
        SELECT
            contact_id,
            company_id,
            contact_name,
            customer_profile,
            notes
        FROM contact_company_profiles
        WHERE contact_id = 'c1'
          AND company_id = 5
        """
    )
    row = cursor.fetchone()

    assert row is not None
    assert row["contact_id"] == "c1"
    assert row["company_id"] == 5
    assert row["contact_name"] == "Cliente Legacy"
    assert row["customer_profile"] is None
    assert row["notes"] is None


def test_close_postgres_pool_closes_and_resets_global_pool() -> None:
    class FakePool:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_pool = FakePool()
    db._POSTGRES_POOL = fake_pool

    db.close_postgres_pool()

    assert fake_pool.closed is True
    assert db._POSTGRES_POOL is None
