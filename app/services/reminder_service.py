import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.company_context import get_company_context
from app.core.config import settings
from app.services.outbound_dispatch_service import dispatch_outbound_message
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


class ReminderServiceError(Exception):
    pass


def _resolve_company_context(company_id: int | None) -> dict[str, Any]:
    if company_id is None:
        return get_company_context()

    get_company = getattr(repository, "get_company", None)
    company = get_company(company_id) if callable(get_company) else None
    company_slug = company.get("slug") if company else None
    return get_company_context(company_slug=company_slug)


def _get_timezone(company_context: dict[str, Any]) -> ZoneInfo:
    timezone_name = (
        company_context.get("business_hours", {}).get("timezone")
        or settings.calendar_timezone
    )
    return ZoneInfo(str(timezone_name))


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def _parse_internal_reminder_time(message_text: str, timezone_obj: ZoneInfo) -> datetime:
    normalized = _normalize_text(message_text).lower()
    now = datetime.now(timezone_obj)

    day_offset = 0
    if "depois de amanhã" in normalized or "depois de amanha" in normalized:
        day_offset = 2
    elif "amanhã" in normalized or "amanha" in normalized:
        day_offset = 1

    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", normalized)
    if date_match:
        base_date = datetime.fromisoformat(date_match.group(1)).date()
    else:
        base_date = (now + timedelta(days=day_offset)).date()

    time_match = re.search(r"(?:às|as|de)\s*(\d{1,2})(?::(\d{2}))?\s*(?:h|hs|horas?)?\b", normalized)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "00")
    else:
        hour = 9
        minute = 0

    return datetime(
        year=base_date.year,
        month=base_date.month,
        day=base_date.day,
        hour=hour,
        minute=minute,
        tzinfo=timezone_obj,
    )


def _extract_internal_reminder_summary(message_text: str) -> str:
    normalized = _normalize_text(message_text)
    patterns = [
        r"me lembra(?:r|)\s+de\s+(.+)",
        r"me lembre\s+de\s+(.+)",
        r"lembrete\s+para\s+(.+)",
        r"me avisa(?:r|)\s+de\s+(.+)",
        r"me avise\s+de\s+(.+)",
    ]
    lowered = normalized.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return normalized[match.start(1):].strip(" .")
    return normalized


def _build_outbound_message(reminder: dict[str, Any]) -> str:
    summary = _normalize_text(str(reminder.get("reminder_summary") or ""))
    if summary:
        return summary
    return _normalize_text(str(reminder.get("reminder_text") or "Lembrete"))


def _resolve_reminder_channel(
    reminder: dict[str, Any],
    explicit_channel: str | None = None,
) -> str | None:
    if explicit_channel:
        return explicit_channel

    company_context = _resolve_company_context(reminder.get("company_id"))
    reminder_policy = company_context.get("reminder_policy", {})
    audience = str(reminder.get("audience") or "")

    if audience == "customer":
        configured = (
            reminder_policy.get("customer_default_channel")
            or reminder_policy.get("default_channel")
        )
        return str(configured or settings.outbound_default_channel)

    configured = (
        reminder_policy.get("internal_default_channel")
        or reminder_policy.get("default_channel")
    )
    return str(configured or settings.outbound_default_channel)


def list_reminders(
    company_id: int | None = None,
    status: str | None = None,
    audience: str | None = None,
) -> list[dict[str, Any]]:
    return repository.get_customer_reminders(
        company_id=company_id,
        status=status,
        audience=audience,
    )


def create_internal_reminder(
    *,
    company_id: int | None,
    owner_contact_id: str,
    message_text: str,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    company_context = _resolve_company_context(company_id)
    reminder_policy = company_context.get("reminder_policy", {})
    if not reminder_policy.get("enabled") or not reminder_policy.get("internal_manual_reminders_enabled", True):
        raise ReminderServiceError("Lembretes internos não estão habilitados para esta empresa.")

    timezone_obj = ZoneInfo(timezone_name or str(_get_timezone(company_context)))
    remind_at = _parse_internal_reminder_time(message_text, timezone_obj)
    reminder_text = _extract_internal_reminder_summary(message_text)
    reminder_id = repository.create_customer_reminder(
        company_id=company_id,
        contact_id=owner_contact_id,
        reminder_text=reminder_text,
        reminder_summary=f"Lembrete interno: {reminder_text}",
        remind_at=remind_at.astimezone(UTC).isoformat(),
        timezone=str(timezone_obj),
        audience="internal",
        source="owner_manual",
    )
    reminder = repository.get_customer_reminder(reminder_id)
    if not reminder:
        raise ReminderServiceError("Reminder interno não encontrado após criação.")
    return reminder


def _get_booking_local_datetime(booking: dict[str, Any], timezone_name: str) -> datetime:
    return datetime.fromisoformat(str(booking["starts_at"])).astimezone(ZoneInfo(timezone_name))


def _build_customer_booking_reminder_message(
    booking: dict[str, Any],
    company_context: dict[str, Any],
) -> str:
    timezone_name = str(booking.get("timezone") or _get_timezone(company_context))
    starts_at = _get_booking_local_datetime(booking, timezone_name)
    company_name = company_context.get("company_name", "a empresa")
    return (
        f"Lembrete do seu agendamento com {company_name}: "
        f"{booking['title']} em {starts_at.strftime('%d/%m às %H:%M')}."
    )


def schedule_booking_customer_reminders(booking: dict[str, Any]) -> list[dict[str, Any]]:
    company_id = booking.get("company_id")
    company_context = _resolve_company_context(company_id)
    reminder_policy = company_context.get("reminder_policy", {})
    if not reminder_policy.get("enabled") or not reminder_policy.get("appointment_customer_reminders_enabled", False):
        return []

    booking_id = int(booking["id"])
    existing = repository.get_customer_reminders_for_booking(booking_id, audience="customer")
    if existing:
        return existing

    offsets = reminder_policy.get("customer_reminder_offsets_days", [])
    if not isinstance(offsets, list):
        return []

    timezone_name = str(booking.get("timezone") or _get_timezone(company_context))
    starts_at = _get_booking_local_datetime(booking, timezone_name)
    created: list[dict[str, Any]] = []

    for offset in sorted({int(value) for value in offsets if isinstance(value, int) and value >= 0}, reverse=True):
        remind_at = starts_at - timedelta(days=offset)
        if remind_at <= datetime.now(ZoneInfo(timezone_name)):
            continue

        reminder_id = repository.create_customer_reminder(
            company_id=company_id,
            contact_id=str(booking["contact_id"]),
            reminder_text=_build_customer_booking_reminder_message(booking, company_context),
            reminder_summary=(
                f"Lembrete automático de agendamento para {starts_at.strftime('%d/%m às %H:%M')} "
                f"({offset} dia(s) antes)."
            ),
            remind_at=remind_at.astimezone(UTC).isoformat(),
            timezone=timezone_name,
            audience="customer",
            source="appointment_policy",
            appointment_booking_id=booking_id,
        )
        reminder = repository.get_customer_reminder(reminder_id)
        if reminder:
            created.append(reminder)

    return created


def dispatch_due_reminders(
    *,
    dispatch_immediately: bool = False,
    channel: str | None = None,
    audience: str | None = None,
) -> list[dict[str, Any]]:
    due_reminders = repository.get_due_customer_reminders(datetime.now(UTC).isoformat())
    results: list[dict[str, Any]] = []

    for reminder in due_reminders:
        if audience and reminder.get("audience") != audience:
            continue

        outbound_message_id = repository.create_outbound_message(
            contact_id=str(reminder["contact_id"]),
            company_id=reminder.get("company_id"),
            message=_build_outbound_message(reminder),
            source=f"reminder_{reminder.get('audience') or 'generic'}",
            status="pending",
        )
        repository.mark_customer_reminder_sent(
            int(reminder["id"]),
            outbound_message_id=outbound_message_id,
        )

        result: dict[str, Any] = {
            "reminder_id": reminder["id"],
            "outbound_message_id": outbound_message_id,
            "contact_id": reminder["contact_id"],
            "audience": reminder.get("audience"),
            "channel": _resolve_reminder_channel(reminder, explicit_channel=channel),
            "status": "queued",
        }
        if dispatch_immediately:
            dispatch_result = dispatch_outbound_message(
                outbound_message_id=outbound_message_id,
                channel=result["channel"],
                company_id=reminder.get("company_id"),
            )
            result["status"] = "sent"
            result["dispatch_result"] = dispatch_result
        results.append(result)

    return results
