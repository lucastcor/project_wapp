"""Migration 073 - espera por job na fila de entrada (revisão P04, F03/F06).

Um envio com falha transitória devolvia o job como `ready` imediatamente, e o
`claim_next` global (menor id elegível) o reivindicava de novo a cada ciclo,
impedindo o worker de atender outras empresas até o backoff do outbound vencer.
`available_at` adia a próxima reivindicação desse job; `dispatch_attempts` limita
as devoluções antes de mandar a conversa para revisão.
"""
from typing import Any

from app.storage.schema_utils import ensure_column


def add_inbound_retry_schedule(cursor: Any) -> None:
    ensure_column(cursor, "inbound_jobs", "available_at", "TEXT")
    ensure_column(cursor, "inbound_jobs", "dispatch_attempts", "INTEGER NOT NULL DEFAULT 0")


RECOVERY_MIGRATIONS = [
    (
        "073_inbound_retry_schedule",
        "Delay retried inbound dispatch jobs and cap their attempts",
        add_inbound_retry_schedule,
    ),
]
