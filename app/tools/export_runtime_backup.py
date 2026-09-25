import argparse
import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.storage.db import (
    close_postgres_pool,
    db_session,
    db_transaction,
    get_database_backend,
    init_db,
)
from app.tools.portability_security import sanitize_runtime_row

# Parent-before-child for inserts and child-before-parent for deletes.
# `schema_migrations` is exported as provenance but remains owned by init_db().
BACKUP_TABLES: list[str] = [
    "companies",
    "saas_users",
    "company_memberships",
    "user_invitations",
    "user_sessions",
    "password_reset_tokens",
    "company_contexts",
    "whatsapp_channel_bindings",
    "contacts",
    "contact_company_profiles",
    "messages",
    "pending_questions",
    "knowledge_entries",
    "message_examples",
    "outbound_messages",
    "worker_leases",
    "finetuning_jobs",
    "audit_events",
    "calendar_integrations",
    # Recurso antes do agendamento que o referencia (070).
    "booking_resources",
    "appointment_bookings",
    "conversation_flows",
    "customer_reminders",
    "appointment_queue_entries",
    "appointment_queue_offers",
    "billing_plans",
    "billing_subscriptions",
    "billing_usage_records",
    "billing_webhook_events",
    "inbound_message_receipts",
    # Plataforma multiempresa (migrations 038-060).
    "service_accounts",
    "service_account_tokens",
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
    # Automation Studio (migrations 061-066). Control plane antes do data
    # plane: uma execução referencia a versão do workflow que a originou.
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_drafts",
    "automation_deployments",
    "automation_events",
    "automation_outbox",
    "automation_executions",
    "automation_step_executions",
    "automation_jobs",
    "automation_effects",
    "automation_approval_gates",
    "inbound_jobs",
    "booking_operations",
    "whatsapp_contact_states",
    "contact_consents",
    "whatsapp_templates",
    "whatsapp_delivery_events",
    "conversation_ownerships",
    "worker_heartbeats",
    "schema_migrations",
]
BACKUP_TABLE_SET = set(BACKUP_TABLES)
TABLE_ORDER_BY = {
    "companies": "id ASC",
    "saas_users": "id ASC",
    "company_memberships": "id ASC",
    "user_invitations": "id ASC",
    "user_sessions": "id ASC",
    "password_reset_tokens": "id ASC",
    "company_contexts": "id ASC",
    "whatsapp_channel_bindings": "id ASC",
    "contacts": "contact_id ASC",
    "contact_company_profiles": "company_id ASC, contact_id ASC",
    "messages": "id ASC",
    "pending_questions": "id ASC",
    "knowledge_entries": "id ASC",
    "message_examples": "id ASC",
    "outbound_messages": "id ASC",
    "worker_leases": "lease_name ASC",
    "finetuning_jobs": "id ASC",
    "audit_events": "id ASC",
    "calendar_integrations": "id ASC",
    "booking_resources": "id ASC",
    "appointment_bookings": "id ASC",
    "conversation_flows": "id ASC",
    "customer_reminders": "id ASC",
    "appointment_queue_entries": "id ASC",
    "appointment_queue_offers": "id ASC",
    "billing_plans": "id ASC",
    "billing_subscriptions": "id ASC",
    "billing_usage_records": "id ASC",
    "billing_webhook_events": "id ASC",
    "inbound_message_receipts": "id ASC",
    "service_accounts": "id ASC",
    "service_account_tokens": "id ASC",
    "company_channel_bindings": "id ASC",
    "company_context_versions": "id ASC",
    "knowledge_documents": "id ASC",
    "knowledge_document_versions": "id ASC",
    "knowledge_chunks": "id ASC",
    "conversation_import_batches": "id ASC",
    "imported_conversations": "id ASC",
    "imported_messages": "id ASC",
    "training_candidates": "id ASC",
    "dataset_versions": "id ASC",
    "dataset_version_items": "id ASC",
    "evaluation_cases": "id ASC",
    "evaluation_runs": "id ASC",
    "evaluation_results": "id ASC",
    "company_models": "id ASC",
    "automation_workflows": "id ASC",
    "automation_workflow_versions": "id ASC",
    "automation_workflow_drafts": "id ASC",
    "automation_deployments": "id ASC",
    "automation_events": "id ASC",
    "automation_outbox": "id ASC",
    "automation_executions": "id ASC",
    "automation_step_executions": "id ASC",
    "automation_jobs": "id ASC",
    "automation_effects": "id ASC",
    "automation_approval_gates": "id ASC",
    "inbound_jobs": "id ASC",
    "booking_operations": "id ASC",
    "whatsapp_contact_states": "company_id ASC, contact_id ASC",
    "contact_consents": "id ASC",
    "whatsapp_templates": "id ASC",
    "whatsapp_delivery_events": "id ASC",
    "conversation_ownerships": "company_id ASC, contact_id ASC",
    "worker_heartbeats": "component ASC, instance_id ASC",
    "schema_migrations": "version ASC",
}
RESTORABLE_TABLES: list[str] = [
    table for table in BACKUP_TABLES if table != "schema_migrations"
]
IDENTITY_TABLES = {
    table_name
    for table_name, ordering in TABLE_ORDER_BY.items()
    if ordering == "id ASC" and table_name != "schema_migrations"
}
BACKUP_FORMAT_VERSION = 4
CHECKSUM_ALGORITHM = "sha256"
EPHEMERAL_BACKUP_TABLES = {
    "user_invitations",
    "user_sessions",
    "password_reset_tokens",
    "worker_leases",
    "worker_heartbeats",
}
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(f"Invalid SQL identifier in backup: {identifier!r}")
    return f'"{identifier}"'


def _serialize_json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _serialize_json_compatible(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_serialize_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_json_compatible(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _table_columns(cursor: Any, table_name: str, *, backend: str) -> list[str]:
    _quote_identifier(table_name)
    if backend == "postgres":
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return [str(row["column_name"]) for row in cursor.fetchall()]

    cursor.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
    return [str(row["name"]) for row in cursor.fetchall()]


def _checksum_payload(payload: dict[str, Any]) -> str:
    protected = {
        key: value for key, value in payload.items() if key != "content_checksum"
    }
    try:
        canonical = json.dumps(
            protected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Backup payload contains values that cannot be checksummed."
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


def _begin_consistent_snapshot(cursor: Any, *, backend: str) -> None:
    if backend == "postgres":
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    else:
        cursor.execute("BEGIN")


def build_backup_payload() -> dict[str, Any]:
    """Build a deterministic, credential-sanitized backup from one DB snapshot."""
    init_db()
    backend = get_database_backend()
    tables: dict[str, list[dict[str, Any]]] = {}
    table_counts: dict[str, int] = {}
    schema_manifest: dict[str, list[str]] = {}
    sanitized_rows = 0
    sensitive_fields_removed = 0
    integrations_disabled = 0
    integration_ids_requiring_reconfiguration: list[int | str] = []

    with db_session() as connection:
        cursor = connection.cursor()
        _begin_consistent_snapshot(cursor, backend=backend)

        for table_name in BACKUP_TABLES:
            quoted_table = _quote_identifier(table_name)
            schema_manifest[table_name] = _table_columns(
                cursor, table_name, backend=backend
            )
            if table_name in EPHEMERAL_BACKUP_TABLES:
                # Opaque session/action token hashes are deliberately not
                # portable. A restored environment starts with every session,
                # invitation and reset link invalidated.
                raw_rows: list[Any] = []
            else:
                query = f"SELECT * FROM {quoted_table}"  # nosec B608
                order_by = TABLE_ORDER_BY[table_name]
                query = f"{query} ORDER BY {order_by}"  # nosec B608
                cursor.execute(query)
                raw_rows = list(cursor.fetchall())

            exported_rows: list[dict[str, Any]] = []
            for raw_row in raw_rows:
                serialized = _serialize_json_compatible(dict(raw_row))
                sanitized, removed_count, integration_disabled = sanitize_runtime_row(
                    table_name,
                    serialized,
                )
                if removed_count:
                    sanitized_rows += 1
                    sensitive_fields_removed += removed_count
                if integration_disabled:
                    integrations_disabled += 1
                    integration_id = sanitized.get("id")
                    if isinstance(integration_id, (int, str)):
                        integration_ids_requiring_reconfiguration.append(integration_id)
                exported_rows.append(sanitized)

            tables[table_name] = exported_rows
            table_counts[table_name] = len(exported_rows)

    payload: dict[str, Any] = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "database_backend": backend,
        "table_counts": table_counts,
        "schema_manifest": schema_manifest,
        "sanitization": {
            "rows_sanitized": sanitized_rows,
            "sensitive_fields_removed": sensitive_fields_removed,
            "integrations_disabled": integrations_disabled,
            "integration_ids_requiring_reconfiguration": (
                integration_ids_requiring_reconfiguration
            ),
            "ephemeral_tables_excluded": sorted(EPHEMERAL_BACKUP_TABLES),
        },
        "tables": tables,
    }
    payload["content_checksum"] = {
        "algorithm": CHECKSUM_ALGORITHM,
        "value": _checksum_payload(payload),
    }
    return payload


def _parse_format_version(payload: dict[str, Any], *, allow_legacy: bool) -> int:
    raw_version = payload.get("backup_format_version")
    if raw_version is None:
        if not allow_legacy:
            raise ValueError(
                "Versionless backup is legacy; pass allow_legacy=True (or --allow-legacy) "
                "after reviewing its contents."
            )
        return 1
    if isinstance(raw_version, bool) or not isinstance(raw_version, (int, str)):
        raise ValueError("backup_format_version must be a positive integer.")
    if isinstance(raw_version, str) and not raw_version.isdigit():
        raise ValueError("backup_format_version must be a positive integer.")
    version = int(raw_version)
    if version < 1:
        raise ValueError("backup_format_version must be a positive integer.")
    if version > BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"Backup format version {version} is newer than this tool supports "
            f"({BACKUP_FORMAT_VERSION}). Upgrade before restoring."
        )
    if version < BACKUP_FORMAT_VERSION and not allow_legacy:
        raise ValueError(
            f"Backup format version {version} is legacy; pass allow_legacy=True "
            "(or --allow-legacy) after reviewing its contents."
        )
    return version


def _validate_integrity_metadata(
    payload: dict[str, Any], *, format_version: int
) -> None:
    checksum = payload.get("content_checksum")
    if format_version < 2 and checksum is None:
        return
    if not isinstance(checksum, dict):
        raise ValueError("Current-format backup is missing content_checksum.")
    if checksum.get("algorithm") != CHECKSUM_ALGORITHM:
        raise ValueError(
            f"Unsupported backup checksum algorithm: {checksum.get('algorithm')!r}."
        )
    provided = checksum.get("value")
    if not isinstance(provided, str) or not re.fullmatch(r"[0-9a-f]{64}", provided):
        raise ValueError("Backup content_checksum value is malformed.")
    expected = _checksum_payload(payload)
    if not hmac.compare_digest(provided, expected):
        raise ValueError(
            "Backup checksum mismatch; the file is corrupt or was modified."
        )


def _validate_payload_structure(
    payload: dict[str, Any],
    *,
    truncate_first: bool,
    allow_legacy: bool,
    partial_restore: bool,
) -> tuple[int, dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
    if not isinstance(payload, dict):
        raise ValueError("Backup payload must be a JSON object.")

    format_version = _parse_format_version(payload, allow_legacy=allow_legacy)
    _validate_integrity_metadata(payload, format_version=format_version)

    raw_tables = payload.get("tables")
    if not isinstance(raw_tables, dict):
        raise ValueError("Backup payload does not contain a 'tables' object.")
    unknown_tables = sorted(set(raw_tables) - BACKUP_TABLE_SET)
    if unknown_tables:
        raise ValueError(
            f"Backup contains unsupported tables: {', '.join(unknown_tables)}"
        )

    if not partial_restore:
        missing_tables = sorted(BACKUP_TABLE_SET - set(raw_tables))
        if missing_tables:
            raise ValueError(
                "Full restore requires every backup table; missing: "
                + ", ".join(missing_tables)
            )

    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name, raw_rows in raw_tables.items():
        _quote_identifier(table_name)
        if not isinstance(raw_rows, list):
            raise ValueError(
                f"Backup table {table_name!r} must contain a list of rows."
            )
        normalized_rows: list[dict[str, Any]] = []
        expected_row_columns: set[str] | None = None
        for index, raw_row in enumerate(raw_rows):
            if not isinstance(raw_row, dict):
                raise ValueError(f"Backup row {table_name}[{index}] must be an object.")
            columns = list(raw_row)
            if any(not isinstance(column, str) for column in columns):
                raise ValueError(
                    f"Backup row {table_name}[{index}] has a non-string column name."
                )
            for column in columns:
                _quote_identifier(column)
            row_columns = set(columns)
            if len(row_columns) != len(columns):
                raise ValueError(
                    f"Backup row {table_name}[{index}] has duplicate columns."
                )
            if expected_row_columns is None:
                expected_row_columns = row_columns
            elif row_columns != expected_row_columns:
                raise ValueError(
                    f"Backup table {table_name!r} contains inconsistent row schemas."
                )
            normalized_row = dict(raw_row)
            _sanitized, removed_count, _integration_disabled = sanitize_runtime_row(
                table_name,
                normalized_row,
            )
            if removed_count:
                raise ValueError(
                    f"Backup table {table_name!r} contains sensitive configuration; "
                    "refusing to restore plaintext credentials."
                )
            normalized_rows.append(normalized_row)
        tables[table_name] = normalized_rows

    raw_counts = payload.get("table_counts")
    if format_version >= 2 and not isinstance(raw_counts, dict):
        raise ValueError("Current-format backup is missing table_counts.")
    if raw_counts is not None:
        if not isinstance(raw_counts, dict):
            raise ValueError("table_counts must be an object.")
        if format_version >= 2 and set(raw_counts) != set(tables):
            raise ValueError("table_counts keys do not match the backup tables.")
        for table_name, expected_count in raw_counts.items():
            if table_name not in tables:
                raise ValueError(f"table_counts contains unknown table {table_name!r}.")
            if (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count < 0
            ):
                raise ValueError(
                    f"table_counts[{table_name!r}] must be a non-negative integer."
                )
            if expected_count != len(tables[table_name]):
                raise ValueError(
                    f"table_counts mismatch for {table_name}: "
                    f"declared {expected_count}, found {len(tables[table_name])}."
                )

    raw_manifest = payload.get("schema_manifest")
    if format_version >= 2 and not isinstance(raw_manifest, dict):
        raise ValueError("Current-format backup is missing schema_manifest.")
    schema_manifest: dict[str, list[str]] = {}
    if raw_manifest is not None:
        if not isinstance(raw_manifest, dict):
            raise ValueError("schema_manifest must be an object.")
        if format_version >= 2 and set(raw_manifest) != set(tables):
            raise ValueError("schema_manifest keys do not match the backup tables.")
        for table_name, raw_columns in raw_manifest.items():
            if table_name not in tables:
                raise ValueError(
                    f"schema_manifest contains unknown table {table_name!r}."
                )
            if not isinstance(raw_columns, list) or any(
                not isinstance(column, str) for column in raw_columns
            ):
                raise ValueError(
                    f"schema_manifest[{table_name!r}] must be a list of columns."
                )
            if len(raw_columns) != len(set(raw_columns)):
                raise ValueError(
                    f"schema_manifest[{table_name!r}] contains duplicate columns."
                )
            for column in raw_columns:
                _quote_identifier(column)
            schema_manifest[table_name] = list(raw_columns)
            for index, row in enumerate(tables[table_name]):
                if set(row) != set(raw_columns):
                    raise ValueError(
                        f"Backup row {table_name}[{index}] does not match its schema manifest."
                    )

    return format_version, tables, schema_manifest


def _validate_target_columns(
    cursor: Any,
    *,
    backend: str,
    tables: dict[str, list[dict[str, Any]]],
    schema_manifest: dict[str, list[str]],
) -> dict[str, list[str]]:
    columns_by_table: dict[str, list[str]] = {}
    for table_name, rows in tables.items():
        allowed_columns = _table_columns(cursor, table_name, backend=backend)
        if not allowed_columns:
            raise ValueError(f"Target database is missing backup table {table_name!r}.")
        allowed_set = set(allowed_columns)
        payload_columns = schema_manifest.get(table_name)
        if payload_columns is None and rows:
            payload_columns = list(rows[0])
        payload_columns = payload_columns or []
        unknown_columns = sorted(set(payload_columns) - allowed_set)
        if unknown_columns:
            raise ValueError(
                f"Backup table {table_name!r} contains unsupported columns: "
                + ", ".join(unknown_columns)
            )
        columns_by_table[table_name] = payload_columns
    return columns_by_table


def _resynchronize_identity_sequences(
    cursor: Any,
    *,
    restored_tables: set[str],
    backend: str,
) -> None:
    if backend != "postgres":
        return
    for table_name in sorted(restored_tables & IDENTITY_TABLES):
        quoted_table = _quote_identifier(table_name)
        cursor.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence(?, 'id'),
                COALESCE((SELECT MAX(id) FROM {quoted_table}), 1),
                COALESCE((SELECT MAX(id) IS NOT NULL FROM {quoted_table}), false)
            )
            """,  # nosec B608
            (table_name,),
        )
        cursor.fetchone()


def restore_backup_payload(
    payload: dict[str, Any],
    *,
    truncate_first: bool = True,
    allow_legacy: bool = False,
    partial_restore: bool = False,
) -> dict[str, Any]:
    """Validate completely, then restore data and sequences in one transaction."""
    format_version, tables, schema_manifest = _validate_payload_structure(
        payload,
        truncate_first=truncate_first,
        allow_legacy=allow_legacy,
        partial_restore=partial_restore,
    )

    init_db()
    backend = get_database_backend()
    restored_counts: dict[str, int] = {}

    with db_transaction() as connection:
        cursor = connection.cursor()
        columns_by_table = _validate_target_columns(
            cursor,
            backend=backend,
            tables=tables,
            schema_manifest=schema_manifest,
        )
        selected_tables = [
            table_name
            for table_name in RESTORABLE_TABLES
            if table_name in tables
            and (partial_restore or table_name in RESTORABLE_TABLES)
        ]

        if truncate_first:
            for table_name in reversed(selected_tables):
                cursor.execute(f"DELETE FROM {_quote_identifier(table_name)}")  # nosec B608

        for table_name in selected_tables:
            rows = tables[table_name]
            restored_counts[table_name] = 0
            if not rows:
                continue
            columns = columns_by_table[table_name]
            if not columns:
                raise ValueError(
                    f"Backup table {table_name!r} has rows but no column manifest."
                )
            placeholders = ", ".join("?" for _ in columns)
            quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
            insert_sql = (
                f"INSERT INTO {_quote_identifier(table_name)} "  # nosec B608
                f"({quoted_columns}) VALUES ({placeholders})"
            )
            for row in rows:
                cursor.execute(insert_sql, tuple(row[column] for column in columns))
            restored_counts[table_name] = len(rows)

        _resynchronize_identity_sequences(
            cursor,
            restored_tables=set(selected_tables),
            backend=backend,
        )

    return {
        "backup_format_version": format_version,
        "database_backend": backend,
        "restored_counts": restored_counts,
        "sanitization": payload.get("sanitization") or {},
    }


def restore_backup_file(
    backup_path: str,
    *,
    truncate_first: bool = True,
    allow_legacy: bool = False,
    partial_restore: bool = False,
) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError(f"Backup JSON contains duplicate key {key!r}.")
            parsed[key] = value
        return parsed

    try:
        payload = json.loads(
            Path(backup_path).read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Backup file is not valid JSON: {exc.msg}.") from exc
    return restore_backup_payload(
        payload,
        truncate_first=truncate_first,
        allow_legacy=allow_legacy,
        partial_restore=partial_restore,
    )


def _atomic_private_write(destination: Path, contents: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        file_descriptor, raw_temporary_path = tempfile.mkstemp(
            dir=str(destination.parent),
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_temporary_path)
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = None
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        try:
            directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    # Not every filesystem supports synchronizing directories.
                    pass
            finally:
                os.close(directory_descriptor)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def write_backup_file(output_path: str | None = None) -> dict[str, Any]:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    destination = (
        Path(output_path)
        if output_path
        else Path(settings.operations_backup_dir) / f"runtime-backup-{timestamp}.json"
    )
    payload = build_backup_payload()
    _atomic_private_write(
        destination,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return {
        "output_path": str(destination.resolve()),
        "database_backend": payload["database_backend"],
        "table_counts": payload["table_counts"],
        "sanitization": payload["sanitization"],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exporta ou restaura um backup lógico do runtime database."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Arquivo de saída do backup. Se omitido, grava em OPERATIONS_BACKUP_DIR.",
    )
    parser.add_argument(
        "--restore",
        default=None,
        metavar="BACKUP_FILE",
        help="Restaura o arquivo indicado no banco configurado em DATABASE_URL.",
    )
    parser.add_argument(
        "--keep-existing-rows",
        action="store_true",
        help="Não apaga as linhas existentes antes de inserir; conflitos continuam sendo erros.",
    )
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="Autoriza explicitamente um backup anterior ao formato atual.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Restaura somente as tabelas presentes; tabelas omitidas não são apagadas.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    try:
        if args.restore:
            result = restore_backup_file(
                args.restore,
                truncate_first=not args.keep_existing_rows,
                allow_legacy=args.allow_legacy,
                partial_restore=args.partial,
            )
        else:
            result = write_backup_file(output_path=args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        # CLI processes have no ASGI lifespan hook. Close worker threads from
        # psycopg_pool explicitly so backup/restore exits promptly and cleanly.
        close_postgres_pool()


if __name__ == "__main__":
    main()
