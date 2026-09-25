"""End-to-end backup and restore of production-critical tenant configuration."""

import copy
import json
import os
from uuid import uuid4

import pytest

from app.core import company_context as company_context_module
from app.core.company_context import get_company_context, save_company_context
from app.core.config import settings
from app.storage.db import db_session, get_database_backend, init_db
from app.storage.repository import RuntimeRepository
from app.tools.export_runtime_backup import (
    BACKUP_FORMAT_VERSION,
    BACKUP_TABLES,
    RESTORABLE_TABLES,
    _atomic_private_write,
    _begin_consistent_snapshot,
    _checksum_payload,
    build_backup_payload,
    restore_backup_file,
    restore_backup_payload,
    write_backup_file,
)

repository = RuntimeRepository()


@pytest.fixture
def seeded_installation():
    """Two tenants with contexts, contact profiles and active/inactive bindings."""
    suffix = uuid4().hex[:10]
    base_context = copy.deepcopy(get_company_context())

    company_a = repository.create_company(slug=f"br-a-{suffix}", name="Backup Tenant A")
    company_b = repository.create_company(slug=f"br-b-{suffix}", name="Backup Tenant B")

    for _company_id, slug, label in [
        (company_a, f"br-a-{suffix}", "Tenant A Context"),
        (company_b, f"br-b-{suffix}", "Tenant B Context"),
    ]:
        payload = copy.deepcopy(base_context)
        payload["company_name"] = label
        save_company_context(payload, company_slug=slug, updated_by="backup-fixture")

    # Same contact id in both tenants: restore must keep them independent.
    shared_contact = f"br-contact-{suffix}"
    repository.save_contact(shared_contact, "Alice A", company_id=company_a)
    repository.save_contact(shared_contact, "Alice B", company_id=company_b)
    repository.update_contact(
        shared_contact,
        contact_name="Alice A",
        company_id=company_a,
        customer_profile="vip",
        notes="tenant a note",
    )
    repository.save_message(
        contact_id=shared_contact,
        company_id=company_a,
        message="mensagem tenant a",
        classification="faq",
        reply="ok",
    )

    active_phone = f"br-active-{suffix}"
    inactive_phone = f"br-inactive-{suffix}"
    repository.upsert_whatsapp_binding(
        company_id=company_a,
        phone_number_id=active_phone,
        display_phone_number="5511900001111",
        updated_by="backup-fixture",
    )
    historical = repository.upsert_whatsapp_binding(
        company_id=company_b,
        phone_number_id=inactive_phone,
        display_phone_number="5511900002222",
        updated_by="backup-fixture",
    )
    repository.set_whatsapp_binding_active(int(historical["id"]), active=False)

    return {
        "suffix": suffix,
        "company_a": company_a,
        "company_b": company_b,
        "slug_a": f"br-a-{suffix}",
        "slug_b": f"br-b-{suffix}",
        "contact_id": shared_contact,
        "active_phone": active_phone,
        "inactive_phone": inactive_phone,
    }


# ── Coverage ──────────────────────────────────────────────────────────


def test_backup_covers_every_runtime_table() -> None:
    """A table added by DDL or ensure helpers must not be silently omitted."""
    with db_session() as connection:
        cursor = connection.cursor()
        if get_database_backend() == "postgres":
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_type = 'BASE TABLE'
                """
            )
            schema_tables = {str(row["table_name"]) for row in cursor.fetchall()}
        else:
            cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            )
            schema_tables = {str(row["name"]) for row in cursor.fetchall()}

    missing = sorted(schema_tables - set(BACKUP_TABLES))
    stale = sorted(set(BACKUP_TABLES) - schema_tables)
    assert missing == [], f"tables missing from BACKUP_TABLES: {missing}"
    assert stale == [], f"BACKUP_TABLES contains unknown tables: {stale}"


def test_tenant_configuration_tables_are_included() -> None:
    for table in ["company_contexts", "whatsapp_channel_bindings", "contact_company_profiles"]:
        assert table in BACKUP_TABLES


def test_backup_declares_its_format_version() -> None:
    payload = build_backup_payload()

    assert payload["backup_format_version"] == BACKUP_FORMAT_VERSION == 4
    assert set(payload["tables"]) == set(BACKUP_TABLES)
    assert set(payload["schema_manifest"]) == set(BACKUP_TABLES)
    assert payload["content_checksum"]["algorithm"] == "sha256"


def test_backup_ordering_is_deterministic(seeded_installation) -> None:
    first = build_backup_payload()
    second = build_backup_payload()

    for table in BACKUP_TABLES:
        assert first["tables"][table] == second["tables"][table], f"{table} ordering is unstable"


@pytest.mark.parametrize(
    ("backend", "expected_sql"),
    [
        ("sqlite", "BEGIN"),
        (
            "postgres",
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        ),
    ],
)
def test_backup_uses_one_backend_appropriate_snapshot(backend, expected_sql) -> None:
    class RecordingCursor:
        statements: list[str] = []

        def execute(self, statement: str) -> None:
            self.statements.append(statement)

    cursor = RecordingCursor()
    _begin_consistent_snapshot(cursor, backend=backend)

    assert cursor.statements == [expected_sql]


def test_backup_contains_no_secrets(seeded_installation, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_token", "super-secret-access-token")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "super-secret-app-secret")
    monkeypatch.setattr(settings, "admin_superadmin_token", "super-secret-admin-token")
    monkeypatch.setattr(settings, "platform_session_secret", "super-secret-session-secret")

    serialized = json.dumps(build_backup_payload())

    for secret in [
        "super-secret-access-token",
        "super-secret-app-secret",
        "super-secret-admin-token",
        "super-secret-session-secret",
    ]:
        assert secret not in serialized


def test_backup_sanitizes_persisted_nested_calendar_credentials(seeded_installation) -> None:
    integration_id = repository.create_calendar_integration(
        provider="google_calendar",
        company_id=seeded_installation["company_a"],
        external_calendar_id="primary",
        config={
            "calendar_id": "primary",
            "client_id": "public-client-id",
            "client_secret": "PERSISTED_CLIENT_SECRET_SENTINEL",
            "nested": [
                {
                    "refresh-token": "PERSISTED_REFRESH_TOKEN_SENTINEL",
                    "phone_number_id": "public-phone-id",
                }
            ],
        },
        active=True,
    )

    payload = build_backup_payload()
    serialized = json.dumps(payload)

    assert "PERSISTED_CLIENT_SECRET_SENTINEL" not in serialized
    assert "PERSISTED_REFRESH_TOKEN_SENTINEL" not in serialized
    exported = next(
        row for row in payload["tables"]["calendar_integrations"] if row["id"] == integration_id
    )
    config = json.loads(exported["config_json"])
    assert config["calendar_id"] == "primary"
    assert config["client_id"] == "public-client-id"
    assert config["nested"][0]["phone_number_id"] == "public-phone-id"
    assert config["_credentials_redacted"] is True
    assert exported["active"] == 0
    assert payload["sanitization"]["sensitive_fields_removed"] >= 2
    assert payload["sanitization"]["integrations_disabled"] >= 1
    assert integration_id in payload["sanitization"][
        "integration_ids_requiring_reconfiguration"
    ]


# ── End-to-end restore ────────────────────────────────────────────────


def test_backup_restores_into_a_clean_database(seeded_installation, tmp_path, monkeypatch) -> None:
    expected = build_backup_payload()
    original_slug_a = seeded_installation["slug_a"]
    original_context_a = get_company_context(company_slug=original_slug_a)["company_name"]

    # Restore into a brand-new database.
    clean_db = tmp_path / "restored.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXTS_DIR", tmp_path / "no-seeds")
    monkeypatch.setattr(company_context_module, "COMPANY_CONTEXT_PATH", tmp_path / "no-global.json")
    init_db()

    result = restore_backup_payload(expected)

    # 1. Companies
    companies = {c["slug"]: c for c in repository.get_companies()}
    assert seeded_installation["slug_a"] in companies
    assert seeded_installation["slug_b"] in companies

    # 2. Company contexts, per tenant
    assert get_company_context(company_slug=original_slug_a)["company_name"] == original_context_a
    assert get_company_context(company_slug=seeded_installation["slug_b"])["company_name"] == "Tenant B Context"

    # 3. Tenant-specific contact profiles stay independent
    company_a = companies[seeded_installation["slug_a"]]["id"]
    company_b = companies[seeded_installation["slug_b"]]["id"]
    contact_a = repository.get_contact(seeded_installation["contact_id"], company_id=company_a)
    contact_b = repository.get_contact(seeded_installation["contact_id"], company_id=company_b)
    assert contact_a["customer_profile"] == "vip"
    assert contact_a["notes"] == "tenant a note"
    assert contact_b["customer_profile"] != "vip"

    # 4. Bindings keep active state and ownership
    active = repository.get_whatsapp_binding_by_phone_number_id(seeded_installation["active_phone"])
    assert active is not None
    assert active["company_id"] == company_a
    assert active["active"] == 1

    inactive_rows = [
        b
        for b in repository.get_whatsapp_bindings()
        if b["phone_number_id"] == seeded_installation["inactive_phone"]
    ]
    assert len(inactive_rows) == 1
    assert inactive_rows[0]["active"] == 0
    assert inactive_rows[0]["company_id"] == company_b

    # 5. Operational data
    assert [m for m in repository.get_messages(company_id=company_a) if m["message"] == "mensagem tenant a"]

    assert result["restored_counts"]["whatsapp_channel_bindings"] >= 2
    assert result["restored_counts"]["company_contexts"] >= 2
    assert result["restored_counts"]["contact_company_profiles"] >= 2


def test_restore_preserves_active_binding_uniqueness(seeded_installation, tmp_path, monkeypatch) -> None:
    payload = build_backup_payload()
    clean_db = tmp_path / "unique.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    restore_backup_payload(payload)

    bindings = repository.get_whatsapp_bindings()
    active_per_phone: dict[str, int] = {}
    for binding in bindings:
        if binding["active"] == 1:
            phone = binding["phone_number_id"]
            active_per_phone[phone] = active_per_phone.get(phone, 0) + 1
    assert all(count == 1 for count in active_per_phone.values())

    # The restored constraint is still enforced afterwards.
    company_b = next(c for c in repository.get_companies() if c["slug"] == seeded_installation["slug_b"])["id"]
    assert repository.upsert_whatsapp_binding(
        company_id=company_b,
        phone_number_id=seeded_installation["active_phone"],
    ) is None


def test_restore_is_idempotent(seeded_installation, tmp_path, monkeypatch) -> None:
    payload = build_backup_payload()
    clean_db = tmp_path / "idempotent.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    first = restore_backup_payload(payload)
    second = restore_backup_payload(payload)

    assert first["restored_counts"] == second["restored_counts"]
    assert len(repository.get_companies()) == len(payload["tables"]["companies"])


def test_restore_requires_explicit_legacy_and_partial_modes(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    payload = build_backup_payload()
    legacy = {
        key: value
        for key, value in payload.items()
        if key not in {"backup_format_version", "content_checksum", "schema_manifest"}
    }
    legacy["tables"] = {
        table: rows
        for table, rows in legacy["tables"].items()
        if table not in {"whatsapp_channel_bindings", "contact_company_profiles"}
    }
    legacy["table_counts"] = {
        table: count
        for table, count in legacy["table_counts"].items()
        if table in legacy["tables"]
    }

    clean_db = tmp_path / "legacy.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    with pytest.raises(ValueError, match="Versionless backup is legacy"):
        restore_backup_payload(legacy)
    with pytest.raises(ValueError, match="requires every backup table"):
        restore_backup_payload(legacy, allow_legacy=True)

    result = restore_backup_payload(
        legacy,
        allow_legacy=True,
        partial_restore=True,
    )

    assert "whatsapp_channel_bindings" not in result["restored_counts"]
    assert result["restored_counts"]["companies"] >= 2


def test_restore_rejects_a_newer_format(tmp_path, monkeypatch) -> None:
    clean_db = tmp_path / "newer.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    with pytest.raises(ValueError, match="newer than this tool supports"):
        restore_backup_payload({"backup_format_version": 99, "tables": {}})


@pytest.mark.parametrize("invalid_version", [True, 0, -1, 1.5, "2.5", "invalid"])
def test_restore_rejects_malformed_format_versions(invalid_version) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        restore_backup_payload(
            {"backup_format_version": invalid_version, "tables": {}},
            allow_legacy=True,
            partial_restore=True,
        )


def test_restore_rejects_unknown_tables(tmp_path, monkeypatch) -> None:
    clean_db = tmp_path / "unknown.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    with pytest.raises(ValueError, match="unsupported tables"):
        restore_backup_payload(
            {"tables": {"sqlite_master": []}},
            allow_legacy=True,
            partial_restore=True,
        )


def test_restore_order_is_foreign_key_safe() -> None:
    """Parents must precede children on insert (and follow them on delete)."""
    order = {table: index for index, table in enumerate(RESTORABLE_TABLES)}

    assert order["companies"] < order["company_contexts"]
    assert order["companies"] < order["whatsapp_channel_bindings"]
    assert order["companies"] < order["contact_company_profiles"]
    assert order["contacts"] < order["contact_company_profiles"]
    assert order["contacts"] < order["messages"]
    assert order["billing_plans"] < order["billing_subscriptions"]


def test_backup_file_round_trip(seeded_installation, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "operations_backup_dir", str(tmp_path))
    written = write_backup_file()

    clean_db = tmp_path / "roundtrip.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()

    result = restore_backup_file(written["output_path"])

    assert result["database_backend"] == get_database_backend()
    assert result["restored_counts"]["companies"] >= 2


# ── Validation, atomicity and private files ───────────────────────────


def _refresh_checksum(payload: dict) -> None:
    payload["content_checksum"] = {
        "algorithm": "sha256",
        "value": _checksum_payload(payload),
    }


def test_restore_rejects_checksum_mismatch_before_database_change(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    payload = build_backup_payload()
    payload["tables"]["companies"][0]["name"] = "tampered"
    clean_db = tmp_path / "checksum.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="checksum-sentinel", name="Untouched")

    with pytest.raises(ValueError, match="checksum mismatch"):
        restore_backup_payload(payload)

    assert repository.get_company(sentinel)["name"] == "Untouched"


def test_current_full_restore_rejects_missing_tables(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    payload = build_backup_payload()
    payload["tables"].pop("messages")
    payload["table_counts"].pop("messages")
    payload["schema_manifest"].pop("messages")
    _refresh_checksum(payload)
    clean_db = tmp_path / "incomplete.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="incomplete-sentinel", name="Untouched")

    with pytest.raises(ValueError, match="requires every backup table"):
        restore_backup_payload(payload)

    assert repository.get_company(sentinel)["name"] == "Untouched"


def test_restore_rejects_injected_column_before_database_change(
    tmp_path,
    monkeypatch,
) -> None:
    clean_db = tmp_path / "injection.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="restore-injection", name="Original")
    injected_column = (
        "name) VALUES (?, ?, ?) ON CONFLICT(id) "
        "DO UPDATE SET name='INJECTED' --"
    )
    payload = {
        "tables": {
            "companies": [
                {
                    "id": sentinel,
                    "slug": "unused",
                    injected_column: "unused",
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        restore_backup_payload(
            payload,
            truncate_first=False,
            allow_legacy=True,
            partial_restore=True,
        )

    assert repository.get_company(sentinel)["name"] == "Original"


def test_restore_rejects_unknown_valid_column_before_database_change(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    payload = build_backup_payload()
    for row in payload["tables"]["companies"]:
        row["future_column"] = "unexpected"
    payload["schema_manifest"]["companies"].append("future_column")
    _refresh_checksum(payload)

    clean_db = tmp_path / "unknown-column.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="unknown-column-sentinel", name="Original")

    with pytest.raises(ValueError, match="unsupported columns: future_column"):
        restore_backup_payload(payload)

    assert repository.get_company(sentinel)["name"] == "Original"


def test_restore_rejects_non_list_rows_and_inconsistent_schemas() -> None:
    with pytest.raises(ValueError, match="must contain a list"):
        restore_backup_payload(
            {"tables": {"companies": {}}},
            allow_legacy=True,
            partial_restore=True,
        )

    with pytest.raises(ValueError, match="inconsistent row schemas"):
        restore_backup_payload(
            {
                "tables": {
                    "companies": [
                        {"id": 1, "slug": "first", "name": "First"},
                        {"id": 2, "slug": "second"},
                    ]
                }
            },
            truncate_first=False,
            allow_legacy=True,
            partial_restore=True,
        )


def test_restore_rejects_table_count_mismatch(seeded_installation) -> None:
    payload = build_backup_payload()
    payload["table_counts"]["companies"] += 1
    _refresh_checksum(payload)

    with pytest.raises(ValueError, match="table_counts mismatch"):
        restore_backup_payload(payload)


def test_restore_rejects_plaintext_credentials_even_with_valid_checksum(
    seeded_installation,
) -> None:
    integration_id = repository.create_calendar_integration(
        provider="google_calendar",
        company_id=seeded_installation["company_a"],
        config={"calendar_id": "primary"},
        active=False,
    )
    payload = build_backup_payload()
    row = next(
        item
        for item in payload["tables"]["calendar_integrations"]
        if item["id"] == integration_id
    )
    row["config_json"] = json.dumps(
        {
            "calendar_id": "primary",
            "refresh_token": "RESTORE_SECRET_SENTINEL",
        }
    )
    _refresh_checksum(payload)

    with pytest.raises(ValueError, match="sensitive configuration"):
        restore_backup_payload(payload)


def test_partial_restore_does_not_delete_omitted_tables(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    # `billing_plans` é catálogo: não referencia empresa. A escolha é
    # deliberada — restaurar parcialmente uma tabela tenant-scoped num banco
    # limpo falha, e falha *corretamente*, porque o trigger de integridade de
    # tenant recusa linhas que apontam para empresas inexistentes. Usar uma
    # tabela sem referência prova a propriedade em questão ("o restore parcial
    # não apaga as tabelas omitidas") sem depender de quais empresas existem no
    # banco de origem.
    full_payload = build_backup_payload()
    partial = copy.deepcopy(full_payload)
    partial["tables"] = {"billing_plans": full_payload["tables"]["billing_plans"]}
    partial["table_counts"] = {"billing_plans": len(partial["tables"]["billing_plans"])}
    partial["schema_manifest"] = {
        "billing_plans": full_payload["schema_manifest"]["billing_plans"]
    }
    _refresh_checksum(partial)

    clean_db = tmp_path / "partial.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="partial-sentinel", name="Untouched")

    restore_backup_payload(partial, partial_restore=True)

    assert repository.get_company(sentinel)["name"] == "Untouched"


def test_restore_rolls_back_deletes_when_insert_fails(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    payload = build_backup_payload()
    duplicate = dict(payload["tables"]["companies"][0])
    duplicate["id"] = max(row["id"] for row in payload["tables"]["companies"]) + 10_000
    payload["tables"]["companies"].append(duplicate)
    payload["table_counts"]["companies"] += 1
    _refresh_checksum(payload)

    clean_db = tmp_path / "rollback.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="rollback-sentinel", name="Untouched")

    with pytest.raises(Exception, match="UNIQUE"):
        restore_backup_payload(payload)

    assert repository.get_company(sentinel)["name"] == "Untouched"


def test_sequence_failure_rolls_back_restore(
    seeded_installation,
    tmp_path,
    monkeypatch,
) -> None:
    import app.tools.export_runtime_backup as backup_module

    payload = build_backup_payload()
    clean_db = tmp_path / "sequence-rollback.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{clean_db}")
    init_db()
    sentinel = repository.create_company(slug="sequence-sentinel", name="Untouched")

    def fail_sequence_sync(*args, **kwargs):
        raise RuntimeError("injected sequence failure")

    monkeypatch.setattr(
        backup_module,
        "_resynchronize_identity_sequences",
        fail_sequence_sync,
    )
    with pytest.raises(RuntimeError, match="injected sequence failure"):
        restore_backup_payload(payload)

    assert repository.get_company(sentinel)["name"] == "Untouched"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_backup_file_is_atomic_and_private(seeded_installation, tmp_path) -> None:
    destination = tmp_path / "private-backup.json"
    destination.write_text("old", encoding="utf-8")
    destination.chmod(0o644)

    written = write_backup_file(str(destination))

    assert written["output_path"] == str(destination.resolve())
    assert destination.stat().st_mode & 0o777 == 0o600
    assert json.loads(destination.read_text(encoding="utf-8"))["backup_format_version"] == 4
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_atomic_write_failure_preserves_existing_destination(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "existing.json"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        _atomic_private_write(destination, "replacement")

    assert destination.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_restore_file_rejects_corrupt_json(tmp_path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        restore_backup_file(str(corrupt))


def test_restore_file_rejects_duplicate_json_keys(tmp_path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"backup_format_version": 2, "backup_format_version": 1, "tables": {}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        restore_backup_file(str(duplicate))
