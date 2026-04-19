from collections.abc import Callable
from typing import Any

from fastapi import Request

from app.core.logging import get_request_context


def build_prometheus_metrics(summary: dict[str, Any]) -> str:
    lines: list[str] = []

    def add_metric(name: str, value: int, labels: dict[str, str] | None = None) -> None:
        if labels:
            label_text = ",".join(f'{key}="{value_}"' for key, value_ in labels.items())
            lines.append(f"{name}{{{label_text}}} {value}")
        else:
            lines.append(f"{name} {value}")

    for key, value in summary.get("totals", {}).items():
        add_metric("project_wapp_total", int(value), {"entity": str(key)})

    grouped_metric_map = {
        "pending_questions_by_status": ("project_wapp_pending_questions", "status"),
        "outbound_by_status": ("project_wapp_outbound_messages", "status"),
        "knowledge_by_status": ("project_wapp_knowledge_entries", "status"),
        "messages_by_classification": ("project_wapp_messages_by_classification", "classification"),
        "messages_by_action": ("project_wapp_messages_by_action", "action"),
        "messages_by_reply_source": ("project_wapp_messages_by_reply_source", "reply_source"),
        "companies_by_status": ("project_wapp_companies", "status"),
        "audit_events_by_status": ("project_wapp_audit_events", "status"),
    }

    for summary_key, (metric_name, label_name) in grouped_metric_map.items():
        for group_key, count in summary.get(summary_key, {}).items():
            add_metric(metric_name, int(count), {label_name: str(group_key)})

    return "\n".join(lines) + "\n"


def record_audit_event(
    *,
    request: Request,
    action: str,
    resource_type: str,
    repository_getter: Callable[[], Any],
    resource_id: str | None = None,
    company_id: int | None = None,
    status: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_id = getattr(request.state, "admin_actor", "admin")
    request_id = get_request_context().get("request_id")
    user_agent = str(request.headers.get("User-Agent") or "").strip() or None
    repository_getter().create_audit_event(
        actor_id=actor_id,
        admin_role=getattr(request.state, "admin_role", None),
        auth_mode=getattr(request.state, "admin_auth_mode", None),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        company_id=company_id,
        status=status,
        request_id=str(request_id) if request_id else None,
        http_method=request.method,
        http_path=request.url.path,
        client_ip=str(get_request_context().get("client_ip") or "") or None,
        user_agent=user_agent,
        metadata=metadata or {},
    )
