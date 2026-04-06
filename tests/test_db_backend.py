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
