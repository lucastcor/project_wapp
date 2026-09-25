"""Durable WhatsApp inbox worker. Run separately from the API, including locally."""
import argparse
import logging
import secrets
import time

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, log_event
from app.services.inbound_service import run_inbound_iteration
from app.services.runtime_guard_service import validate_runtime_environment
from app.services.worker_health_service import record_heartbeat, remove_heartbeat, worker_is_alive
from app.storage.db import check_database_schema_current, close_postgres_pool, init_db

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    owner = secrets.token_urlsafe(18)
    started = False
    configure_logging()
    try:
        if args.healthcheck:
            if not settings.inbound_worker_enabled or not worker_is_alive("inbound"):
                raise SystemExit(1)
            return
        if settings.run_migrations_on_startup:
            init_db()
        elif not check_database_schema_current()["ok"]:
            raise RuntimeError("Run app.tools.run_migrations before starting the inbound worker.")
        validate_runtime_environment("inbound_worker")
        if args.validate_only or not settings.inbound_worker_enabled:
            return
        started = True
        while True:
            record_heartbeat("inbound", owner)
            processed = False
            try:
                result = run_inbound_iteration()
                record_heartbeat("inbound", owner)
                processed = bool(result["processed"])
                if processed:
                    log_event(logger, logging.INFO, "inbound.worker.processed", job_id=result["job_id"])
            except Exception:
                logger.exception("inbound.worker.iteration_failed")
                if args.run_once:
                    raise
            if args.run_once:
                return
            # Drene o backlog sem acrescentar um intervalo artificial entre
            # jobs. O polling configurado só é necessário quando a fila está
            # vazia ou uma iteração falhou.
            if not processed:
                time.sleep(max(0.1, settings.inbound_worker_poll_seconds))
    finally:
        try:
            if started:
                remove_heartbeat("inbound", owner)
        finally:
            close_postgres_pool()


if __name__ == "__main__":
    main()
