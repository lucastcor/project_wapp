"""Migrations: banco limpo, banco já migrado e reexecução segura."""

import sqlite3
from pathlib import Path

import pytest

from app.core.config import settings
from app.storage import db as db_module
from app.storage.db import MIGRATIONS, init_db

IDENTITY_TABLES = {
    "platform_principals",
    "platform_access_tokens",
    "platform_company_memberships",
    "tenant_api_credentials",
}

PLATFORM_TABLES = {
    "company_channel_bindings",
    "company_context_versions",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "conversation_import_batches",
    "imported_conversations",
    "imported_messages",
    "training_candidates",
    "dataset_versions",
    "dataset_version_items",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
    "company_models",
}


@pytest.fixture()
def temp_database(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "migrations.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    yield database_path


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def _columns(database_path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        connection.close()
    return {row[1] for row in rows}


def test_migrations_run_on_a_clean_database(temp_database: Path) -> None:
    init_db()

    tables = _table_names(temp_database)
    assert tables >= PLATFORM_TABLES
    assert "schema_migrations" in tables


def test_migrations_are_idempotent(temp_database: Path) -> None:
    init_db()
    init_db()
    init_db()

    connection = sqlite3.connect(temp_database)
    try:
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        connection.close()

    assert applied == len(MIGRATIONS)


def test_migrations_upgrade_an_existing_database(temp_database: Path) -> None:
    """Simula um banco já migrado até a última versão legada (023)."""
    legacy_migrations = [
        migration for migration in MIGRATIONS if migration[0] <= "023_contact_company_profile_indexes"
    ]
    assert legacy_migrations

    connection = db_module.get_connection()
    cursor = connection.cursor()
    db_module._ensure_schema_migrations_table(cursor)
    for version, name, migration_fn in legacy_migrations:
        migration_fn(cursor)
        db_module._record_migration(cursor, version, name)
    connection.commit()
    connection.close()

    tables_before = _table_names(temp_database)
    assert "message_examples" in tables_before
    assert not (PLATFORM_TABLES & tables_before)

    init_db()

    tables_after = _table_names(temp_database)
    assert tables_after >= PLATFORM_TABLES
    # Colunas aditivas aplicadas sobre tabela pré-existente.
    assert {"customer_profile", "approval_status", "source_reference"} <= _columns(
        temp_database, "message_examples"
    )
    assert {"dataset_version_id", "readiness_json"} <= _columns(temp_database, "finetuning_jobs")
    assert "retrieval_json" in _columns(temp_database, "messages")


def test_existing_data_survives_the_upgrade(temp_database: Path) -> None:
    legacy_migrations = [
        migration for migration in MIGRATIONS if migration[0] <= "023_contact_company_profile_indexes"
    ]
    connection = db_module.get_connection()
    cursor = connection.cursor()
    db_module._ensure_schema_migrations_table(cursor)
    for version, name, migration_fn in legacy_migrations:
        migration_fn(cursor)
        db_module._record_migration(cursor, version, name)
    cursor.execute(
        """
        INSERT INTO message_examples (
            profile_name, scenario_type, customer_message, approved_reply, quality_score
        )
        VALUES ('default', 'lead', 'pergunta legada', 'resposta legada', 9.0)
        """
    )
    connection.commit()
    connection.close()

    init_db()

    connection = sqlite3.connect(temp_database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT customer_message, approval_status FROM message_examples"
        ).fetchone()
    finally:
        connection.close()

    assert row["customer_message"] == "pergunta legada"
    # Linhas legadas herdam o default aprovado para não quebrar contratos.
    assert row["approval_status"] == "approved"


def test_identity_migrations_run_on_a_clean_database(temp_database: Path) -> None:
    init_db()

    tables = _table_names(temp_database)
    assert tables >= IDENTITY_TABLES
    assert {"source_group_key", "conversation_context_json", "source_candidate_id"} <= _columns(
        temp_database, "message_examples"
    )
    assert {"execution_mode", "promotion_eligible", "case_suite_hash"} <= _columns(
        temp_database, "evaluation_runs"
    )
    assert {"current_published_version_id", "working_version_id"} <= _columns(
        temp_database, "knowledge_documents"
    )
    assert {"job_status", "failure_reason"} <= _columns(temp_database, "company_models")
    assert "promotion_state" in _columns(temp_database, "training_candidates")


def test_upgrade_from_migration_037(temp_database: Path) -> None:
    """Banco parado na entrega anterior deve subir sem perder dados."""
    previous_migrations = [
        migration for migration in MIGRATIONS if migration[0] <= "037_message_retrieval_trace"
    ]
    assert previous_migrations

    connection = db_module.get_connection()
    cursor = connection.cursor()
    db_module._ensure_schema_migrations_table(cursor)
    for version, name, migration_fn in previous_migrations:
        migration_fn(cursor)
        db_module._record_migration(cursor, version, name)

    cursor.execute(
        """
        INSERT INTO message_examples (
            company_id, profile_name, scenario_type, customer_message,
            approved_reply, quality_score, approval_status
        )
        VALUES (1, 'default', 'lead', 'pergunta 037', 'resposta 037', 9.0, 'approved')
        """
    )
    cursor.execute(
        """
        INSERT INTO knowledge_documents (company_id, title, source_type, document_type, status)
        VALUES (1, 'Doc 037', 'manual', 'general_faq', 'approved')
        """
    )
    connection.commit()
    connection.close()

    tables_before = _table_names(temp_database)
    assert not (IDENTITY_TABLES & tables_before)

    init_db()

    tables_after = _table_names(temp_database)
    assert tables_after >= IDENTITY_TABLES

    connection = sqlite3.connect(temp_database)
    connection.row_factory = sqlite3.Row
    try:
        example = connection.execute(
            "SELECT customer_message, approval_status, source_group_key FROM message_examples"
        ).fetchone()
        document = connection.execute(
            "SELECT title, status, current_published_version_id FROM knowledge_documents"
        ).fetchone()
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        connection.close()

    # Dado antigo intacto; colunas novas apenas acrescentadas.
    assert example["customer_message"] == "pergunta 037"
    assert example["approval_status"] == "approved"
    assert example["source_group_key"] is None
    # Documento já aprovado continua publicado após a separação de versões.
    assert document["status"] == "approved"
    assert applied == len(MIGRATIONS)


def test_identity_migrations_are_idempotent(temp_database: Path) -> None:
    init_db()
    init_db()
    init_db()

    connection = sqlite3.connect(temp_database)
    try:
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        principals = connection.execute("SELECT COUNT(*) FROM platform_principals").fetchone()[0]
    finally:
        connection.close()

    assert applied == len(MIGRATIONS)
    assert principals == 0


# --- 045-053: endurecimento e Automation Studio ------------------------------------


AUTOMATION_TABLES = {
    "automation_workflows",
    "automation_workflow_drafts",
    "automation_workflow_versions",
    "automation_deployments",
    "automation_events",
    "automation_outbox",
    "automation_executions",
    "automation_step_executions",
    "automation_jobs",
    "automation_effects",
    "automation_approval_gates",
}


def test_hardening_and_automation_migrations_run_on_a_clean_database(
    temp_database: Path,
) -> None:
    init_db()

    tables = _table_names(temp_database)
    assert tables >= AUTOMATION_TABLES

    # 045: época de autenticação, que faz revogação valer para sessões vivas.
    assert "auth_epoch" in _columns(temp_database, "platform_principals")
    # 046: metadados de retrieval passam a pertencer à versão do documento.
    assert {
        "title",
        "document_type",
        "priority",
        "valid_from",
        "valid_until",
    } <= _columns(temp_database, "knowledge_document_versions")
    # 047: procedência do executor da avaliação.
    assert {"executor_kind", "provider", "attestation_json"} <= _columns(
        temp_database, "evaluation_runs"
    )
    # Execução presa a uma versão exata.
    assert {"version_id", "enrollment_key", "context_json"} <= _columns(
        temp_database, "automation_executions"
    )
    # Job durável com lease.
    assert {"lease_owner", "lease_expires_at", "run_at", "max_attempts"} <= _columns(
        temp_database, "automation_jobs"
    )


def test_upgrade_from_migration_044(temp_database: Path) -> None:
    """Banco parado na entrega anterior sobe sem perder dados."""
    previous_migrations = [
        migration for migration in MIGRATIONS if migration[0] <= "044_import_lineage_indexes"
    ]
    assert previous_migrations

    connection = db_module.get_connection()
    cursor = connection.cursor()
    db_module._ensure_schema_migrations_table(cursor)
    for version, name, migration_fn in previous_migrations:
        migration_fn(cursor)
        db_module._record_migration(cursor, version, name)

    cursor.execute(
        """
        INSERT INTO knowledge_documents (
            company_id, title, source_type, document_type, status, priority
        )
        VALUES (1, 'Política 044', 'manual', 'business_rule', 'approved', 7)
        """
    )
    document_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO knowledge_document_versions (
            company_id, document_id, version, content, content_hash, status
        )
        VALUES (1, ?, 1, 'Conteúdo anterior à 046.', 'hash-044', 'approved')
        """,
        (document_id,),
    )
    cursor.execute(
        """
        INSERT INTO platform_principals (principal_key, display_name, global_role, is_global)
        VALUES ('principal-044', 'Principal 044', 'operator', 0)
        """
    )
    connection.commit()
    connection.close()

    tables_before = _table_names(temp_database)
    assert not (AUTOMATION_TABLES & tables_before)

    init_db()

    assert _table_names(temp_database) >= AUTOMATION_TABLES

    connection = sqlite3.connect(temp_database)
    connection.row_factory = sqlite3.Row
    try:
        version_row = connection.execute(
            """
            SELECT title, document_type, priority, content
            FROM knowledge_document_versions
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
        principal = connection.execute(
            "SELECT principal_key, global_role, auth_epoch FROM platform_principals"
        ).fetchone()
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        connection.close()

    # 046 faz o retrofit: a versão herda o metadado que o documento carregava,
    # que é a única interpretação possível de dados anteriores à separação.
    assert version_row["title"] == "Política 044"
    assert version_row["document_type"] == "business_rule"
    assert int(version_row["priority"]) == 7
    assert version_row["content"] == "Conteúdo anterior à 046."

    # 045 dá época 1 a quem já existia, sem invalidar nada retroativamente.
    assert principal["principal_key"] == "principal-044"
    assert principal["global_role"] == "operator"
    assert int(principal["auth_epoch"]) == 1

    assert applied == len(MIGRATIONS)


def test_automation_migrations_are_idempotent(temp_database: Path) -> None:
    init_db()
    init_db()
    init_db()

    connection = sqlite3.connect(temp_database)
    try:
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        workflows = connection.execute(
            "SELECT COUNT(*) FROM automation_workflows"
        ).fetchone()[0]
    finally:
        connection.close()

    assert applied == len(MIGRATIONS)
    assert workflows == 0


def test_migration_versions_are_unique_and_ordered() -> None:
    """Duas migrations com a mesma versão fariam uma delas nunca rodar."""
    versions = [version for version, _, _ in MIGRATIONS]

    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)
