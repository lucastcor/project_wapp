import argparse
import json
import sys

from app.services.system_readiness_service import (
    get_system_profile,
    get_system_readiness,
)
from app.storage.db import close_postgres_pool, init_db


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa preflight operacional do Project WAPP.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Falha se overall_status nao for exactly 'ready'.",
    )
    parser.add_argument(
        "--require-scale-ready",
        action="store_true",
        help="Falha se scale_ready nao estiver verdadeiro.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    try:
        init_db()
        payload = {
            "system_profile": get_system_profile(),
            "system_readiness": get_system_readiness(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        readiness = payload["system_readiness"]
        if readiness.get("overall_status") == "blocked":
            return 1
        if args.require_ready and readiness.get("overall_status") != "ready":
            return 1
        if args.require_scale_ready and not readiness.get("scale_ready"):
            return 1
        return 0
    finally:
        close_postgres_pool()


if __name__ == "__main__":
    sys.exit(main())
