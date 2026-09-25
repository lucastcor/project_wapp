"""Transactional booking operations: proposal, confirmation and execution (069)."""
from typing import Any

from app.storage.schema_utils import create_table


def create_booking_operations(cursor: Any) -> None:
    # Parent key for the tenant-bound booking reference below. Migration 035
    # already creates this exact index; repeating it (IF NOT EXISTS, same name)
    # keeps 069 self-sufficient without adding a second unique index to the
    # bookings table.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_appointment_bookings_id_company "
        "ON appointment_bookings (id, company_id)"
    )
    create_table(cursor, "booking_operations", """
            id ID_PK,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            contact_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('create', 'cancel', 'reschedule')),
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'confirmed', 'executing', 'completed', 'rejected',
                                  'expired', 'superseded', 'failed', 'review')),
            source TEXT NOT NULL DEFAULT 'conversation'
                CHECK (source IN ('conversation', 'admin_api', 'operator', 'automation')),
            booking_id INTEGER,
            resource_key TEXT,
            params_json TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            target_version TEXT,
            flow_id INTEGER,
            idempotency_key TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            confirmed_at TEXT,
            confirmation_ref TEXT,
            started_at TEXT,
            completed_at TEXT,
            result_json TEXT,
            last_error TEXT,
            external_ref TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT uq_booking_operations_idempotency UNIQUE (company_id, idempotency_key),
            CONSTRAINT fk_booking_operations_booking_tenant
                FOREIGN KEY (booking_id, company_id) REFERENCES appointment_bookings (id, company_id)
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_booking_operations_contact "
        "ON booking_operations (company_id, contact_id, status, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_booking_operations_status "
        "ON booking_operations (status, updated_at)"
    )
    # At most one open proposal per contact; a new one supersedes the old in
    # the same transaction (operation_repository.create_proposal).
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_operations_open_proposal "
        "ON booking_operations (company_id, contact_id) WHERE status = 'proposed'"
    )
    # A confirmation (provider message id or hash) is consumed at most once per
    # company, so a redelivered acceptance can never confirm a second operation.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_operations_confirmation_ref "
        "ON booking_operations (company_id, confirmation_ref) WHERE confirmation_ref IS NOT NULL"
    )


OPERATION_MIGRATIONS = [
    ("069_booking_operations", "Persist booking proposals and their execution state", create_booking_operations),
]
