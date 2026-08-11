"""Durable, transactional company context persistence."""

import copy
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import company_context as company_context_module
from app.core.company_context import (
    COMPANY_CONTEXT_SCHEMA_VERSION,
    GLOBAL_CONTEXT_KEY,
    CompanyContextConflictError,
    CompanyContextError,
    bootstrap_company_contexts,
    get_company_context,
    get_company_context_record,
    get_company_context_status,
    plan_seed_context_import,
    save_company_context,
    save_company_context_record,
)
from app.core.config import settings
from app.main import app
from app.storage.db import MIGRATIONS, get_database_backend, init_db
from app.storage.repository import RuntimeRepository
from app.tools.export_runtime_backup import BACKUP_TABLES, build_backup_payload

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture
def tenant_slug() -> str:
    return f"ctx-tenant-{uuid4().hex[:10]}"


def _valid_context(company_name: str = "Tenant Co") -> dict:
    context = copy.deepcopy(get_company_context())
    context["company_name"] = company_name
    return context


# ── Migration ─────────────────────────────────────────────────────────


def test_migration_creates_the_table_on_a_fresh_database(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "fresh.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")

    init_db()

    with sqlite3.connect(fresh_db) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(company_contexts)").fetchall()
        }
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert {
        "company_id",
        "company_slug",
        "context_json",
        "schema_version",
        "revision",
        "created_at",
        "updated_at",
        "updated_by",
    } <= columns
    assert {"024_company_contexts", "025_company_context_indexes", "026_company_context_seed_import"} <= applied


def test_migration_is_idempotent(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "idempotent.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")

    init_db()
    init_db()
    init_db()

    with sqlite3.connect(fresh_db) as connection:
        connection.row_factory = sqlite3.Row
        migration_count = connection.execute(
            "SELECT COUNT(*) AS count FROM schema_migrations"
        ).fetchone()["count"]
        context_count = connection.execute(
            "SELECT COUNT(*) AS count FROM company_contexts"
        ).fetchone()["count"]
        global_rows = connection.execute(
            "SELECT COUNT(*) AS count FROM company_contexts WHERE company_slug = ?",
            (GLOBAL_CONTEXT_KEY,),
        ).fetchone()["count"]

    assert migration_count == len(MIGRATIONS)
    assert context_count >= 1
    assert global_rows == 1


def test_migration_versions_are_unique_and_ordered() -> None:
    versions = [version for version, _name, _fn in MIGRATIONS]

    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)


# ── Seed import / bootstrap ───────────────────────────────────────────


def test_seed_files_are_imported_into_a_fresh_database(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "seeded.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")

    init_db()

    record = repository.get_company_context_record(GLOBAL_CONTEXT_KEY)
    assert record is not None
    assert record["revision"] == 1
    assert record["schema_version"] == COMPANY_CONTEXT_SCHEMA_VERSION
    assert str(record["updated_by"]).startswith("seed:")
    assert json.loads(record["context_json"])["company_name"]


def test_bootstrap_is_idempotent_and_never_overwrites_the_database(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "bootstrap.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")
    init_db()

    updated = _valid_context("Depois do Seed")
    save_company_context(updated, updated_by="admin")
    revision_after_update = repository.get_company_context_record(GLOBAL_CONTEXT_KEY)["revision"]

    first = bootstrap_company_contexts()
    second = bootstrap_company_contexts()

    assert first["imported"] == []
    assert GLOBAL_CONTEXT_KEY in first["skipped_existing"]
    assert first == second

    record = repository.get_company_context_record(GLOBAL_CONTEXT_KEY)
    assert json.loads(record["context_json"])["company_name"] == "Depois do Seed"
    assert record["revision"] == revision_after_update


def test_bootstrap_dry_run_plan_reports_without_writing(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "plan.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")
    init_db()

    plan = plan_seed_context_import()

    assert plan["imported"] == []
    assert GLOBAL_CONTEXT_KEY in plan["skipped_existing"]


def test_invalid_seed_files_are_skipped(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "invalid-seed.db"
    seed_dir = tmp_path / "company_contexts"
    seed_dir.mkdir()
    (seed_dir / "broken-tenant.json").write_text('{"company_name": "só isso"}', encoding="utf-8")

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXTS_DIR", seed_dir)

    init_db()

    assert repository.get_company_context_record("broken-tenant") is None


# ── Database precedence over seed files ───────────────────────────────


def test_database_context_takes_precedence_over_seed_file(tmp_path, monkeypatch) -> None:
    fresh_db = tmp_path / "precedence.db"
    seed_dir = tmp_path / "company_contexts"
    seed_dir.mkdir()
    seed_slug = "precedence-tenant"
    seed_payload = _valid_context("Do Arquivo de Seed")
    (seed_dir / f"{seed_slug}.json").write_text(
        json.dumps(seed_payload, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXTS_DIR", seed_dir)
    init_db()

    assert get_company_context(company_slug=seed_slug)["company_name"] == "Do Arquivo de Seed"

    save_company_context(_valid_context("Do Banco"), company_slug=seed_slug, updated_by="admin")

    assert get_company_context(company_slug=seed_slug)["company_name"] == "Do Banco"
    assert get_company_context_status(company_slug=seed_slug)["source"] == "database"


def test_production_runtime_never_falls_back_to_seed_files(tmp_path, monkeypatch) -> None:
    seed_payload = _valid_context("Seed Proibido")
    fresh_db = tmp_path / "production.db"
    seed_dir = tmp_path / "company_contexts"
    seed_dir.mkdir()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{fresh_db}")
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXTS_DIR", seed_dir)
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXT_PATH", tmp_path / "missing.json")
    # The database is migrated before the seed file exists, so nothing is imported.
    init_db()
    (seed_dir / "prod-tenant.json").write_text(
        json.dumps(seed_payload, ensure_ascii=False), encoding="utf-8"
    )

    # Development still reads the file...
    assert get_company_context(company_slug="prod-tenant")["company_name"] == "Seed Proibido"

    # ...production does not.
    monkeypatch.setattr(settings, "environment", "production")
    assert company_context_module.seed_contexts_enabled() is False
    with pytest.raises(CompanyContextError):
        get_company_context(company_slug="prod-tenant")

    status = get_company_context_status(company_slug="prod-tenant")
    assert status["is_valid"] is False
    assert "seed files are disabled" in str(status["error"])


def test_strict_runtime_validation_also_disables_seed_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "strict_runtime_validation", True)

    assert company_context_module.seed_contexts_enabled() is False


# ── Transactions, validation and concurrency ──────────────────────────


def test_invalid_payload_never_replaces_the_last_valid_version(tenant_slug) -> None:
    save_company_context(_valid_context("Versão Boa"), company_slug=tenant_slug, updated_by="admin")

    with pytest.raises(CompanyContextError):
        save_company_context({"company_name": "Quebrado"}, company_slug=tenant_slug)

    record = get_company_context_record(company_slug=tenant_slug)
    assert record["data"]["company_name"] == "Versão Boa"
    assert record["revision"] == 1


def test_revision_increments_on_every_write(tenant_slug) -> None:
    first = save_company_context_record(_valid_context("V1"), company_slug=tenant_slug)
    second = save_company_context_record(_valid_context("V2"), company_slug=tenant_slug)

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert second["data"]["company_name"] == "V2"


def test_stale_revision_is_rejected(tenant_slug) -> None:
    save_company_context_record(_valid_context("V1"), company_slug=tenant_slug)
    save_company_context_record(
        _valid_context("V2"), company_slug=tenant_slug, expected_revision=1
    )

    with pytest.raises(CompanyContextConflictError):
        save_company_context_record(
            _valid_context("Conflito"), company_slug=tenant_slug, expected_revision=1
        )

    assert get_company_context(company_slug=tenant_slug)["company_name"] == "V2"


def test_expected_revision_zero_only_matches_a_missing_row(tenant_slug) -> None:
    created = save_company_context_record(
        _valid_context("Novo"), company_slug=tenant_slug, expected_revision=0
    )
    assert created["revision"] == 1

    with pytest.raises(CompanyContextConflictError):
        save_company_context_record(
            _valid_context("Outro"), company_slug=tenant_slug, expected_revision=0
        )


def test_updated_by_is_persisted(tenant_slug) -> None:
    record = save_company_context_record(
        _valid_context("Com Autor"), company_slug=tenant_slug, updated_by="ops-lead"
    )

    assert record["updated_by"] == "ops-lead"
    assert record["created_at"] is not None
    assert record["updated_at"] is not None


# ── Shared visibility (API and worker) ────────────────────────────────


def test_update_is_immediately_visible_to_a_separate_repository_instance(tenant_slug) -> None:
    save_company_context(_valid_context("Compartilhado"), company_slug=tenant_slug)

    worker_repository = RuntimeRepository()
    row = worker_repository.get_company_context_record(tenant_slug)

    assert json.loads(row["context_json"])["company_name"] == "Compartilhado"


def test_context_survives_an_application_restart(tmp_path, monkeypatch) -> None:
    persistent_db = tmp_path / "restart.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{persistent_db}")
    init_db()
    save_company_context(_valid_context("Sobrevive"), company_slug="restart-tenant")

    # Simulate a fresh process: re-run startup migrations against the same file.
    init_db()

    assert get_company_context(company_slug="restart-tenant")["company_name"] == "Sobrevive"


def test_api_update_is_visible_to_the_worker_context_reader(monkeypatch, tenant_slug) -> None:
    from app.services import reminder_service

    monkeypatch.setattr(settings, "enable_admin_auth", False)
    company_id = repository.create_company(slug=tenant_slug, name="Worker Tenant")

    response = client.put(
        "/admin/company-context",
        params={"company_slug": tenant_slug},
        json={"data": _valid_context("Visto Pelo Worker")},
    )
    assert response.status_code == 200

    worker_view = reminder_service._resolve_company_context(company_id)
    assert worker_view["company_name"] == "Visto Pelo Worker"


# ── Tenant isolation ──────────────────────────────────────────────────


def test_context_reads_and_writes_are_tenant_isolated() -> None:
    slug_a = f"iso-a-{uuid4().hex[:8]}"
    slug_b = f"iso-b-{uuid4().hex[:8]}"

    save_company_context(_valid_context("Empresa A"), company_slug=slug_a)
    save_company_context(_valid_context("Empresa B"), company_slug=slug_b)

    assert get_company_context(company_slug=slug_a)["company_name"] == "Empresa A"
    assert get_company_context(company_slug=slug_b)["company_name"] == "Empresa B"

    save_company_context(_valid_context("Empresa A v2"), company_slug=slug_a)

    assert get_company_context(company_slug=slug_b)["company_name"] == "Empresa B"


def test_context_write_links_the_internal_company_id() -> None:
    slug = f"linked-{uuid4().hex[:8]}"
    company_id = repository.create_company(slug=slug, name="Linked Co")

    record = save_company_context_record(_valid_context("Linked Co"), company_slug=slug)

    assert record["company_id"] == company_id


def test_unknown_tenant_falls_back_to_the_shared_context() -> None:
    status = get_company_context_status(company_slug=f"unknown-{uuid4().hex[:8]}")

    assert status["is_valid"] is True
    assert status["fallback_used"] is True


# ── API surface ───────────────────────────────────────────────────────


def test_company_context_record_endpoint_exposes_the_revision(monkeypatch, tenant_slug) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)
    save_company_context(_valid_context("Com Revisão"), company_slug=tenant_slug)

    response = client.get("/admin/company-context/record", params={"company_slug": tenant_slug})

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 1
    assert payload["source"] == "database"
    assert payload["schema_version"] == COMPANY_CONTEXT_SCHEMA_VERSION
    assert payload["data"]["company_name"] == "Com Revisão"


def test_company_context_update_returns_409_on_revision_conflict(monkeypatch, tenant_slug) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)
    save_company_context(_valid_context("V1"), company_slug=tenant_slug)
    save_company_context(_valid_context("V2"), company_slug=tenant_slug)

    response = client.put(
        "/admin/company-context",
        params={"company_slug": tenant_slug},
        json={"data": _valid_context("Conflito"), "expected_revision": 1},
    )

    assert response.status_code == 409
    assert get_company_context(company_slug=tenant_slug)["company_name"] == "V2"


def test_company_context_update_rejects_invalid_payload(monkeypatch, tenant_slug) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)
    save_company_context(_valid_context("Válido"), company_slug=tenant_slug)

    response = client.put(
        "/admin/company-context",
        params={"company_slug": tenant_slug},
        json={"data": {"company_name": "Incompleto"}},
    )

    assert response.status_code == 400
    assert get_company_context(company_slug=tenant_slug)["company_name"] == "Válido"


def test_company_context_update_is_audited(monkeypatch, tenant_slug) -> None:
    from app.api import admin_routes

    audit_events: list[dict] = []

    class _AuditRepository(RuntimeRepository):
        def create_audit_event(self, **kwargs) -> int:
            audit_events.append(kwargs)
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", False)
    monkeypatch.setattr(admin_routes, "repository", _AuditRepository())

    response = client.put(
        "/admin/company-context",
        params={"company_slug": tenant_slug},
        json={"data": _valid_context("Auditado")},
    )

    assert response.status_code == 200
    context_events = [e for e in audit_events if e.get("resource_type") == "company_context"]
    assert context_events
    assert context_events[-1]["action"] == "update"
    assert context_events[-1]["metadata"]["revision"] == 1
    # The audit trail records shape, not the payload itself.
    assert "data" not in context_events[-1]["metadata"]


# ── Backup ────────────────────────────────────────────────────────────


def test_backup_includes_company_contexts(tmp_path, monkeypatch) -> None:
    backup_db = tmp_path / "backup.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{backup_db}")
    init_db()
    save_company_context(_valid_context("No Backup"), company_slug="backup-tenant")

    payload = build_backup_payload()

    assert "company_contexts" in BACKUP_TABLES
    rows = payload["tables"]["company_contexts"]
    assert payload["table_counts"]["company_contexts"] == len(rows)
    slugs = {row["company_slug"] for row in rows}
    assert "backup-tenant" in slugs
    assert GLOBAL_CONTEXT_KEY in slugs


# ── Backend compatibility ─────────────────────────────────────────────


def test_context_queries_use_backend_agnostic_placeholders() -> None:
    """The repository must rely on `?` placeholders normalized per backend."""
    source = Path("app/storage/repository.py").read_text(encoding="utf-8")
    start = source.index("def get_company_context_record")
    end = source.index("def create_calendar_integration")
    context_sql = source[start:end]

    assert "%s" not in context_sql
    assert "f\"" not in context_sql
    assert get_database_backend() in {"sqlite", "postgres"}
