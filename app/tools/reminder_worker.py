import logging
import time
import argparse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, log_event
from app.services.runtime_guard_service import validate_runtime_environment
from app.services.appointment_queue_service import process_due_queue_offers
from app.services.reminder_service import dispatch_due_reminders
from app.storage.db import init_db


configure_logging()
logger = get_logger(__name__)
VALID_AUDIENCES = {"customer", "internal"}


def _parse_audiences() -> list[str]:
    audiences: list[str] = []
    for item in settings.reminder_worker_audiences.split(","):
        normalized = item.strip().lower()
        if normalized in VALID_AUDIENCES and normalized not in audiences:
            audiences.append(normalized)
    return audiences or ["customer", "internal"]


def run_worker_iteration() -> list[dict]:
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
    return processed


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
    init_db()
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


if __name__ == "__main__":
    main()
