import argparse
import logging
import secrets
import time

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, log_event
from app.services.appointment_queue_service import (
    process_due_queue_offers,
    recover_deferred_queue_offers,
)
from app.services.calendar_service import reconcile_pending_reservations
from app.services.reminder_service import dispatch_due_reminders
from app.services.runtime_guard_service import validate_runtime_environment
from app.storage.db import (
    check_database_schema_current,
    check_runtime_database_connection,
    close_postgres_pool,
    init_db,
)
from app.storage.operation_repository import fail_stale_confirmations
from app.storage.repository import RuntimeRepository

configure_logging()
logger = get_logger(__name__)
VALID_AUDIENCES = {"customer", "internal"}
repository = RuntimeRepository()
WORKER_OWNER_TOKEN = secrets.token_urlsafe(24)


def _parse_audiences() -> list[str]:
    audiences: list[str] = []
    for item in settings.reminder_worker_audiences.split(","):
        normalized = item.strip().lower()
        if normalized in VALID_AUDIENCES and normalized not in audiences:
            audiences.append(normalized)
    return audiences or ["customer", "internal"]


def run_worker_iteration() -> list[dict]:
    acquired = repository.acquire_worker_lease(
        "reminder_worker_iteration",
        WORKER_OWNER_TOKEN,
        ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    if not acquired:
        log_event(
            logger,
            logging.INFO,
            "reminder_worker.iteration.skipped",
            reason="lease_held_by_another_replica",
        )
        return []

    try:
        processed: list[dict] = []
        for audience in _parse_audiences():
            processed.extend(
                dispatch_due_reminders(
                    dispatch_immediately=settings.reminder_worker_dispatch_immediately,
                    channel=settings.reminder_worker_channel or None,
                    audience=audience,
                )
            )
        processed.extend(process_due_queue_offers())
        # Encaixe adiado quando falhou logo após um cancelamento (P04, R02).
        processed.extend(recover_deferred_queue_offers())
        # Reserva `pending` órfã: confirma pelo evento externo ou libera (P04).
        processed.extend(reconcile_pending_reservations())
        # Aceite sem execução (queda entre aceite e claim) não fica pendurado.
        stale_confirmations = fail_stale_confirmations()
        if stale_confirmations:
            processed.append({"action": "stale_confirmations_failed", "count": stale_confirmations})
        return processed
    finally:
        repository.release_worker_lease(
            "reminder_worker_iteration",
            WORKER_OWNER_TOKEN,
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project WAPP reminder worker")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate runtime configuration and exit without processing jobs.",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process a single iteration and exit.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    try:
        if settings.run_migrations_on_startup:
            init_db()
        elif (
            not check_runtime_database_connection()["ok"]
            or not check_database_schema_current()["ok"]
        ):
            raise RuntimeError(
                "Database is unavailable or not migrated. Run "
                "`python -m app.tools.run_migrations` before starting the worker."
            )
        validate_runtime_environment("worker")
        poll_seconds = max(15, settings.reminder_worker_poll_seconds)

        if args.validate_only:
            log_event(
                logger,
                logging.INFO,
                "reminder_worker.validation.ok",
            )
            return

        if not settings.reminder_worker_enabled:
            log_event(
                logger,
                logging.WARNING,
                "reminder_worker.disabled",
                poll_seconds=poll_seconds,
            )
            return

        log_event(
            logger,
            logging.INFO,
            "reminder_worker.started",
            poll_seconds=poll_seconds,
            audiences=_parse_audiences(),
            dispatch_immediately=settings.reminder_worker_dispatch_immediately,
            channel=settings.reminder_worker_channel,
        )

        while True:
            try:
                processed = run_worker_iteration()
                log_event(
                    logger,
                    logging.INFO,
                    "reminder_worker.iteration.completed",
                    processed_count=len(processed),
                )
                if args.run_once:
                    return
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "reminder_worker.iteration.failed",
                    exception_type=type(exc).__name__,
                    error=str(exc),
                )
                if args.run_once:
                    raise
            time.sleep(poll_seconds)
    finally:
        # Healthchecks and one-shot invocations must release psycopg_pool's
        # background workers before Python exits. The long-running worker also
        # benefits from deterministic cleanup on graceful termination.
        close_postgres_pool()


if __name__ == "__main__":
    main()
