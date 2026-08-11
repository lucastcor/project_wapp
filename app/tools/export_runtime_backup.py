import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.storage.db import get_database_backend, init_db, read_connection

BACKUP_TABLES: list[str] = [
    "companies",
    "contacts",
    "messages",
    "pending_questions",
    "knowledge_entries",
    "message_examples",
    "outbound_messages",
    "finetuning_jobs",
    "audit_events",
    "calendar_integrations",
    "appointment_bookings",
    "conversation_flows",
    "customer_reminders",
    "appointment_queue_entries",
    "appointment_queue_offers",
    "billing_plans",
    "billing_subscriptions",
    "billing_usage_records",
    "inbound_message_receipts",
    "schema_migrations",
]
BACKUP_TABLE_SET = set(BACKUP_TABLES)
TABLE_ORDER_BY = {
    "companies": "id ASC",
    "messages": "id ASC",
    "pending_questions": "id ASC",
    "knowledge_entries": "id ASC",
    "message_examples": "id ASC",
    "outbound_messages": "id ASC",
    "finetuning_jobs": "id ASC",
    "audit_events": "id ASC",
    "calendar_integrations": "id ASC",
    "appointment_bookings": "id ASC",
    "conversation_flows": "id ASC",
    "customer_reminders": "id ASC",
    "appointment_queue_entries": "id ASC",
    "appointment_queue_offers": "id ASC",
    "billing_plans": "id ASC",
    "billing_subscriptions": "id ASC",
    "billing_usage_records": "id ASC",
    "inbound_message_receipts": "id ASC",
    "schema_migrations": "version ASC",
}


def _serialize_json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _serialize_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_json_compatible(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def build_backup_payload() -> dict[str, Any]:
    init_db()
    with read_connection() as connection:
        cursor = connection.cursor()

        tables: dict[str, list[dict[str, Any]]] = {}
        table_counts: dict[str, int] = {}
        for table_name in BACKUP_TABLES:
            if table_name not in BACKUP_TABLE_SET:
                raise ValueError(f"Unsupported backup table: {table_name}")
            order_by = TABLE_ORDER_BY.get(table_name)
            order_clause = f" ORDER BY {order_by}" if order_by else ""
            # `table_name` é validado contra BACKUP_TABLE_SET logo acima e
            # `order_clause` vem do mapa literal TABLE_ORDER_BY. A origem literal
            # é verificada em tests/test_sql_interpolation_guard.py.
            cursor.execute(f"SELECT * FROM {table_name}{order_clause}")
            rows = [_serialize_json_compatible(dict(row)) for row in cursor.fetchall()]
            tables[table_name] = rows
            table_counts[table_name] = len(rows)

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "database_backend": get_database_backend(),
            "table_counts": table_counts,
            "tables": tables,
        }


def write_backup_file(output_path: str | None = None) -> dict[str, Any]:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if output_path:
        destination = Path(output_path)
    else:
        destination = Path(settings.operations_backup_dir) / f"runtime-backup-{timestamp}.json"

    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = build_backup_payload()
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "output_path": str(destination.resolve()),
        "database_backend": payload["database_backend"],
        "table_counts": payload["table_counts"],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Exporta um backup lógico do runtime database.")
    parser.add_argument(
        "--output",
        default=None,
        help="Arquivo de saída do backup. Se omitido, grava em OPERATIONS_BACKUP_DIR.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    result = write_backup_file(output_path=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
