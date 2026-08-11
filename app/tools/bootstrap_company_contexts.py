"""Idempotently import the file-based company contexts into the database.

The runtime store of record for company context is the shared database. This
tool imports the JSON seed files under ``app/data`` for a database that does not
have them yet. It never overwrites an existing row, so a newer database context
always wins over an older seed file.

Usage::

    python -m app.tools.bootstrap_company_contexts
    python -m app.tools.bootstrap_company_contexts --dry-run
"""

import argparse
import json

from app.core.company_context import (
    bootstrap_company_contexts,
    plan_seed_context_import,
)
from app.storage.db import init_db


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Importa os company contexts de seed para o banco de runtime.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria importado sem gravar no banco.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    init_db()
    result = plan_seed_context_import() if args.dry_run else bootstrap_company_contexts()
    print(json.dumps({"dry_run": args.dry_run, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
