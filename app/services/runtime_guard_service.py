from typing import Any

from app.core.config import settings
from app.services.system_readiness_service import get_system_readiness


def runtime_validation_enabled() -> bool:
    return settings.strict_runtime_validation or settings.environment.lower() == "production"


def get_runtime_blockers() -> list[dict[str, Any]]:
    readiness = get_system_readiness()
    return [
        check
        for check in readiness.get("checks", [])
        if check.get("status") == "fail" and check.get("severity") == "blocker"
    ]


def validate_runtime_environment(component: str) -> None:
    if not runtime_validation_enabled():
        return

    # Startup validates configuration; liveness belongs to readiness only.
    # Requiring heartbeats here would deadlock the first API/worker deployment.
    blockers = [check for check in get_runtime_blockers()
                if check.get("code") not in {"automation_worker_heartbeat", "inbound_worker_heartbeat"}]
    if not blockers:
        return

    blocker_lines = [
        f"{check.get('code')}: {check.get('detail')}"
        for check in blockers
    ]
    raise RuntimeError(
        f"Runtime validation failed for {component}. "
        + " | ".join(blocker_lines)
    )
