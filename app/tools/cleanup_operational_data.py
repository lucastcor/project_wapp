import argparse
import json
from typing import Any

from app.storage.db import init_db
from app.storage.repository import RuntimeRepository
from app.tools.export_runtime_backup import write_backup_file

repository = RuntimeRepository()


def build_cleanup_plan(stale_after_days: int) -> dict[str, Any]:
    hygiene = repository.get_operational_hygiene_summary(stale_after_days=stale_after_days)
    return {
        "mode": "dry_run",
        "stale_after_days": stale_after_days,
        "candidates": hygiene,
        "proposed_actions": {
            "deactivate_placeholder_companies": hygiene["counts"]["active_placeholder_companies"],
            "archive_stale_pending_questions": hygiene["counts"]["stale_pending_questions"],
            "cancel_stale_outbound_messages": hygiene["counts"]["stale_outbound_messages"],
            "archive_stale_failed_finetuning_jobs": hygiene["counts"]["stale_failed_finetuning_jobs"],
        },
    }


def execute_cleanup(
    *,
    stale_after_days: int,
    backup_output: str | None = None,
    skip_backup: bool = False,
) -> dict[str, Any]:
    plan = build_cleanup_plan(stale_after_days=stale_after_days)
    candidates = plan["candidates"]

    backup_result = None
    has_mutations = any(
        int(plan["proposed_actions"][action_name]) > 0
        for action_name in [
            "deactivate_placeholder_companies",
            "archive_stale_pending_questions",
            "cancel_stale_outbound_messages",
            "archive_stale_failed_finetuning_jobs",
        ]
    )
    if has_mutations and not skip_backup:
        backup_result = write_backup_file(output_path=backup_output)

    deactivated_companies = repository.deactivate_companies(
        [int(company["id"]) for company in candidates["active_placeholder_companies"]]
    )
    archived_pending_questions = repository.archive_pending_questions(
        [int(question["id"]) for question in candidates["stale_pending_questions"]]
    )
    cancelled_outbound_messages = repository.cancel_outbound_messages(
        [int(message["id"]) for message in candidates["stale_outbound_messages"]]
    )
    archived_finetuning_jobs = repository.archive_finetuning_jobs(
        [int(job["id"]) for job in candidates["stale_failed_finetuning_jobs"]]
    )

    return {
        "mode": "execute",
        "stale_after_days": stale_after_days,
        "backup": backup_result,
        "applied": {
            "deactivated_companies": deactivated_companies,
            "archived_pending_questions": archived_pending_questions,
            "cancelled_outbound_messages": cancelled_outbound_messages,
            "archived_finetuning_jobs": archived_finetuning_jobs,
        },
        "remaining_hygiene": repository.get_operational_hygiene_summary(stale_after_days=stale_after_days),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Limpa dados operacionais obsoletos com dry-run por padrão.")
    parser.add_argument(
        "--stale-after-days",
        type=int,
        default=30,
        help="Janela em dias para considerar backlog operacional como obsoleto.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Aplica a limpeza. Sem este flag, executa apenas dry-run.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Nao gerar backup automatico antes da limpeza.",
    )
    parser.add_argument(
        "--backup-output",
        default=None,
        help="Caminho opcional para gravar o backup gerado antes da limpeza.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    init_db()
    stale_after_days = max(1, int(args.stale_after_days))
    if args.execute:
        payload = execute_cleanup(
            stale_after_days=stale_after_days,
            backup_output=args.backup_output,
            skip_backup=args.skip_backup,
        )
    else:
        payload = build_cleanup_plan(stale_after_days=stale_after_days)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
