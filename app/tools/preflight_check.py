import json
import sys

from app.services.system_readiness_service import get_system_profile, get_system_readiness
from app.storage.db import init_db


def main() -> int:
    init_db()
    payload = {
        "system_profile": get_system_profile(),
        "system_readiness": get_system_readiness(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    overall_status = payload["system_readiness"].get("overall_status")
    return 1 if overall_status == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
