"""Resumo operacional por empresa (P06, F06): o que precisa de atenção agora.

Visível no painel/API sem depender de alerta externo. Só contagens, códigos e
idades — nenhum texto de cliente.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.core.tenancy import require_company_id
from app.services.worker_health_service import worker_is_alive
from app.storage.sql_support import fetch_all, fetch_one

STALE_BACKLOG_SECONDS = 300
_ACTIVE_INBOUND_STATUSES = ("pending", "processing", "ready", "dispatching")


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def dead_letter_code(last_error: str | None) -> str:
    """Código agregável de um dead letter (`kind:code:mensagem` do dispatch)."""
    parts = str(last_error or "").split(":", 2)
    if len(parts) != 3:
        return "unclassified"
    kind, code, message = parts
    if message.startswith("blocked:"):
        return message.split()[0]
    return f"{kind}:{code}"


def _count(sql: str, company_id: int) -> int:
    row = fetch_one(sql, (company_id,))
    return int((row or {}).get("n") or 0)


def _purposes_missing_template(company_id: int) -> list[str]:
    """Finalidades com envio agendado/aberto e sem template aprovado no idioma da política.

    Fora da janela de 24 h essas mensagens virariam dead letter
    `outside_window_no_template`; o alerta aparece antes do envio falhar.
    """
    from app.services.whatsapp_delivery_policy import template_language_for
    from app.storage import channel_delivery_repository as channel

    in_use: list[str] = []
    if _count(
        "SELECT COUNT(*) AS n FROM customer_reminders "
        "WHERE company_id = ? AND status = 'pending' AND audience = 'customer'",
        company_id,
    ):
        in_use.append("reminders")
    if _count(
        "SELECT COUNT(*) AS n FROM appointment_queue_offers WHERE company_id = ? AND status IN ('pending', 'waiting')",
        company_id,
    ):
        in_use.append("queue_offers")
    if not in_use:
        return []
    language = template_language_for(company_id)
    missing = []
    for purpose in in_use:
        template = channel.get_template(company_id=company_id, purpose=purpose, language_code=language)
        usable = (
            template is not None
            and template.get("status") == "approved"
            and int(template.get("active") or 0) == 1
            and template.get("category") == "utility"
        )
        if not usable:
            missing.append(purpose)
    return missing


def get_operations_summary(company_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    cid = require_company_id(company_id, context="operations summary")
    moment = now or datetime.now(UTC)

    inbound = {
        str(row["status"]): int(row["n"])
        for row in fetch_all(
            "SELECT status, COUNT(*) AS n FROM inbound_jobs WHERE company_id = ? GROUP BY status",
            (cid,),
        )
    }
    oldest_row = fetch_one(
        """
        SELECT MIN(created_at) AS oldest
        FROM inbound_jobs
        WHERE company_id = ?
          AND status IN ('pending', 'processing', 'ready', 'dispatching')
        """,
        (cid,),
    )
    oldest_at = _parse_timestamp((oldest_row or {}).get("oldest")) if (oldest_row or {}).get("oldest") else None
    oldest_age_seconds = int((moment - oldest_at).total_seconds()) if oldest_at else None

    outbound = {
        str(row["status"]): int(row["n"])
        for row in fetch_all(
            """
            SELECT status, COUNT(*) AS n
            FROM outbound_messages
            WHERE company_id = ?
              AND status IN ('pending', 'retry', 'processing', 'dead_letter')
            GROUP BY status
            """,
            (cid,),
        )
    }
    dead_letters = Counter(
        dead_letter_code(row.get("last_error"))
        for row in fetch_all(
            """
            SELECT last_error
            FROM outbound_messages
            WHERE company_id = ?
              AND status = 'dead_letter'
              -- Resposta retirada ao resolver uma revisão não é falha de envio.
              AND COALESCE(last_error, '') NOT LIKE ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (cid, "%blocked:resolved_manually%"),
        )
    )
    operations = {
        str(row["status"]): int(row["n"])
        for row in fetch_all(
            """
            SELECT status, COUNT(*) AS n
            FROM booking_operations
            WHERE company_id = ?
              AND status IN ('confirmed', 'executing', 'review')
            GROUP BY status
            """,
            (cid,),
        )
    }
    pending_reservations = _count(
        "SELECT COUNT(*) AS n FROM appointment_bookings WHERE company_id = ? AND status = 'pending'", cid
    )
    # Remarcação com resultado externo incerto mantém o novo intervalo retido.
    reschedule_holds = _count(
        "SELECT COUNT(*) AS n FROM appointment_bookings WHERE company_id = ? AND hold_starts_at IS NOT NULL", cid
    )
    human_owned = _count(
        "SELECT COUNT(*) AS n FROM conversation_ownerships WHERE company_id = ? AND owner = 'human'", cid
    )
    open_pending_questions = _count(
        "SELECT COUNT(*) AS n FROM pending_questions WHERE company_id = ? AND status = 'pending'", cid
    )
    inbound_worker_alive = worker_is_alive("inbound")
    backlog = sum(inbound.get(status, 0) for status in ("pending", "ready"))

    alerts: list[dict[str, Any]] = []
    if inbound.get("review"):
        alerts.append({"code": "inbound_review_open", "count": inbound["review"]})
    if oldest_age_seconds is not None and oldest_age_seconds > STALE_BACKLOG_SECONDS:
        alerts.append({"code": "inbound_backlog_stale", "oldest_age_seconds": oldest_age_seconds})
    if backlog and not inbound_worker_alive:
        alerts.append({"code": "inbound_worker_down", "backlog": backlog})
    if dead_letters:
        alerts.append({"code": "outbound_dead_letters", "count": sum(dead_letters.values())})
    if operations.get("review") or operations.get("executing"):
        alerts.append({
            "code": "booking_operations_need_attention",
            "review": operations.get("review", 0),
            "executing": operations.get("executing", 0),
        })
    if pending_reservations:
        alerts.append({"code": "booking_reservations_pending", "count": pending_reservations})
    if reschedule_holds:
        alerts.append({"code": "booking_reschedule_holds_open", "count": reschedule_holds})
    missing_templates = _purposes_missing_template(cid)
    if missing_templates:
        alerts.append({"code": "whatsapp_template_missing", "purposes": missing_templates})

    return {
        "company_id": cid,
        "generated_at": moment.astimezone(UTC).isoformat(),
        "inbound": {
            "by_status": inbound,
            "backlog": backlog,
            "oldest_active_age_seconds": oldest_age_seconds,
            "worker_alive": inbound_worker_alive,
        },
        "outbound": {"by_status": outbound, "dead_letters_by_code": dict(dead_letters)},
        "booking_operations": operations,
        "pending_reservations": pending_reservations,
        "reschedule_holds_open": reschedule_holds,
        "human_owned_conversations": human_owned,
        "open_pending_questions": open_pending_questions,
        "alerts": alerts,
    }
