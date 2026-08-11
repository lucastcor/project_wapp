"""Explicit release-step entrypoint for managed/serverless deployments."""

import json

from app.storage.db import (
    check_database_schema_current,
    close_postgres_pool,
    init_db,
)


def main() -> None:
    try:
        init_db()
        result = check_database_schema_current()
        print(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)
    finally:
        close_postgres_pool()


if __name__ == "__main__":
    main()
