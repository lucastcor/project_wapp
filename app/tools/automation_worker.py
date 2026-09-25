"""Worker do Automation Studio.

Cada iteração faz três coisas, nesta ordem:

1. **reconcilia** — devolve à fila jobs com lease vencido e portões
   abandonados. Vem primeiro para que o trabalho recuperado entre no mesmo
   ciclo;
2. **despacha o outbox** — transforma eventos em inscrições;
3. **executa jobs prontos** — um passo por job.

O worker é global: ele varre a fila sem filtrar empresa, e o isolamento vem
do `company_id` que acompanha cada job. Várias réplicas podem rodar juntas —
o claim é atômico nos dois backends.

Nada aqui roda por padrão: `AUTOMATION_WORKER_ENABLED=false` é o default e o
processo sai avisando.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, log_event
from app.services.automation_event_service import dispatch_pending_events
from app.services.automation_execution_service import recover_stalled_work, run_due_jobs
from app.services.automation_scheduler_service import dispatch_scheduled_workflows
from app.services.runtime_guard_service import validate_runtime_environment
from app.services.worker_health_service import record_heartbeat, remove_heartbeat, worker_is_alive
from app.storage import automation_repository as repo
from app.storage.db import check_database_schema_current, close_postgres_pool, init_db

configure_logging()
logger = get_logger(__name__)


def run_worker_iteration(*, lease_owner: str | None = None, job_limit: int = 10) -> dict[str, Any]:
    """Um ciclo completo. Exposto para o teste e para `--run-once`."""
    owner = lease_owner or repo.new_lease_owner("automation")

    record_heartbeat("automation", owner)
    recovered = recover_stalled_work()
    # O scheduler vem antes do despacho para que o gatilho de agenda emitido
    # neste ciclo já seja processado neste ciclo, e não só no seguinte.
    scheduled = dispatch_scheduled_workflows()
    dispatched = dispatch_pending_events(limit=20, lease_owner=owner)
    executed = run_due_jobs(limit=job_limit, lease_owner=owner)

    record_heartbeat("automation", owner)
    return {
        "lease_owner": owner,
        "recovered": recovered,
        "scheduled": scheduled,
        "dispatched": dispatched,
        "executed": executed,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project WAPP automation worker")
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
    parser.add_argument(
        "--job-limit",
        type=int,
        default=10,
        help="Maximum jobs processed per iteration.",
    )
    parser.add_argument("--healthcheck", action="store_true")
    return parser


def _run() -> None:
    args = _build_arg_parser().parse_args()
    if args.healthcheck:
        try:
            if not settings.automation_engine_enabled or not settings.automation_worker_enabled or not worker_is_alive("automation"):
                raise SystemExit(1)
            return
        finally:
            close_postgres_pool()
    if settings.run_migrations_on_startup:
        init_db()
    elif not check_database_schema_current()["ok"]:
        raise RuntimeError("Run app.tools.run_migrations before starting the automation worker.")
    validate_runtime_environment("worker")

    if not settings.automation_engine_enabled:
        log_event(
            logger,
            logging.WARNING,
            "automation.worker.engine_disabled",
            detail="AUTOMATION_ENGINE_ENABLED is false; nothing to process.",
        )
        return

    if args.validate_only:
        log_event(logger, logging.INFO, "automation.worker.validated")
        return

    if not settings.automation_worker_enabled:
        log_event(
            logger,
            logging.WARNING,
            "automation.worker.disabled",
            detail="AUTOMATION_WORKER_ENABLED is false; not starting the loop.",
        )
        return

    owner = repo.new_lease_owner("automation")
    poll_seconds = max(0.5, float(settings.automation_worker_poll_seconds))

    log_event(
        logger,
        logging.INFO,
        "automation.worker.started",
        lease_owner=owner,
        poll_seconds=poll_seconds,
        run_once=bool(args.run_once),
    )

    try:
        while True:
            try:
                result = run_worker_iteration(lease_owner=owner, job_limit=int(args.job_limit))
                if (
                    result["executed"]["processed"]
                    or result["dispatched"]["dispatched"]
                    or result["scheduled"]["emitted"]
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        "automation.worker.iteration",
                        dispatched=result["dispatched"]["dispatched"],
                        enrolled=result["dispatched"]["enrolled"],
                        processed=result["executed"]["processed"],
                        recovered_jobs=result["recovered"]["recovered_jobs"],
                        scheduled=result["scheduled"]["emitted"],
                    )
            except Exception:
                # O worker não morre por causa de um ciclo ruim: o próximo tenta
                # de novo e os leases vencidos são recuperados.
                logger.exception("automation.worker.iteration_failed")

            if args.run_once:
                return
            time.sleep(poll_seconds)
    finally:
        remove_heartbeat("automation", owner)
        close_postgres_pool()


def main() -> None:
    try:
        _run()
    finally:
        close_postgres_pool()


if __name__ == "__main__":
    main()
