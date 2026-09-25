"""Durable inbound queue and per-process worker heartbeats (068)."""
from typing import Any

from app.storage.schema_utils import create_table


def create_inbound_queue(cursor: Any) -> None:
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_outbound_company_identity ON outbound_messages(company_id, id)")
    create_table(cursor, "inbound_jobs", """
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            provider_message_id TEXT NOT NULL,
            contact_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'ready', 'dispatching', 'completed', 'review')),
            claim_token TEXT,
            lease_expires_at TEXT,
            result_json TEXT,
            outbound_message_id INTEGER,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(company_id, provider_message_id),
            FOREIGN KEY(company_id, outbound_message_id) REFERENCES outbound_messages(company_id, id)
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_inbound_conversation ON inbound_jobs(company_id, contact_id, id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_inbound_claim ON inbound_jobs(status, id)")
    create_table(cursor, "worker_heartbeats", """
            component TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(component, instance_id)
    """)


INBOUND_MIGRATIONS = [("068_durable_inbound_queue", "Persist webhook jobs and worker heartbeats", create_inbound_queue)]
