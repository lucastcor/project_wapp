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
