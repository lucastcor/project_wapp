import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.calendar_service import (
    create_calendar_booking,
    query_calendar_availability,
)
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()
CONFIRMATION_TERMS = {
    "sim",
    "pode confirmar",
    "pode marcar",
    "confirmar",
    "confirmado",
    "fechado",
    "perfeito",
    "combinado",
    "ok",
    "isso",
}
CANCELLATION_TERMS = {
    "cancelar",
    "cancela",
    "deixa pra depois",
    "deixa para depois",
    "não",
    "nao",
}
SCHEDULING_TERMS = [
    "agendar",
    "agendamento",
    "marcar horário",
    "marcar horario",
    "marcar um horário",
    "marcar um horario",
    "horário disponível",
    "horario disponivel",
    "disponibilidade",
    "consulta",
    "reunião",
    "reuniao",
]
WEEKDAY_NAMES = {
    "segunda": 0,
    "terça": 1,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
}


@dataclass
class WorkflowResult:
    handled: bool
    classification: str | None = None
    reply: str = ""
    source: str = "workflow"
    action: str = "workflow"
    priority: str = "high"
    auto_reply: bool = True
    workflow_type: str | None = None
    metadata: dict[str, Any] | None = None


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _enabled_modules(company_context: dict[str, Any]) -> dict[str, Any]:
    modules = company_context.get("enabled_modules", {})
    return modules if isinstance(modules, dict) else {}


def _scheduling_enabled(company_context: dict[str, Any]) -> bool:
    modules = _enabled_modules(company_context)
    return bool(modules.get("scheduling"))


def _get_timezone(company_context: dict[str, Any]) -> ZoneInfo:
    business_hours = company_context.get("business_hours", {})
    timezone_name = business_hours.get("timezone") or settings.calendar_timezone
    return ZoneInfo(str(timezone_name))


def _extract_minimum_notice_minutes(company_context: dict[str, Any]) -> int:
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("minimum_notice_minutes")
    if isinstance(configured, int) and configured >= 0:
        return configured

    window_text = str(company_context.get("business_hours", {}).get("scheduling_window") or "")
    match = re.search(r"(\d+)\s*hora", window_text.lower())
    if match:
        return int(match.group(1)) * 60
    return 120


def _extract_slot_duration_minutes(company_context: dict[str, Any]) -> int:
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("default_slot_duration_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured

    catalog = company_context.get("catalog", {})
    for item in catalog.get("items", []):
        if not isinstance(item, dict):
            continue
        if item.get("schedule_required"):
            duration_text = str(item.get("duration") or "")
            match = re.search(r"(\d+)", duration_text)
            if match:
                return int(match.group(1))
    return 60


def _extract_service_name(company_context: dict[str, Any], message: str) -> str | None:
    normalized = _normalize_text(message)
    catalog = company_context.get("catalog", {})
    items = catalog.get("items", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name.lower() in normalized:
            return name

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("schedule_required"):
            return str(item.get("name") or "").strip() or None
    return None


def _extract_request_summary(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message).strip()
    return cleaned[:240] if cleaned else "Atendimento solicitado via bot"


def _parse_natural_date(message: str, timezone_obj: ZoneInfo) -> date | None:
    normalized = _normalize_text(message)
    today = datetime.now(timezone_obj).date()

    if "depois de amanhã" in normalized or "depois de amanha" in normalized:
        return today + timedelta(days=2)
    if "amanhã" in normalized or "amanha" in normalized:
        return today + timedelta(days=1)
    if "hoje" in normalized:
        return today

    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", normalized)
    if iso_match:
        return date.fromisoformat(iso_match.group(1))

    short_match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", normalized)
    if short_match:
        day = int(short_match.group(1))
        month = int(short_match.group(2))
        year = int(short_match.group(3) or today.year)
        parsed = date(year, month, day)
        if parsed < today:
            parsed = date(year + 1, month, day)
        return parsed

    for weekday_name, weekday_number in WEEKDAY_NAMES.items():
        if weekday_name in normalized:
            days_ahead = (weekday_number - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)

    return None


def _parse_natural_time(message: str) -> tuple[str | None, str | None]:
    normalized = _normalize_text(message)
    time_match = re.search(r"(?:às|as|por volta de|de)\s*(\d{1,2})(?::(\d{2}))?\s*(?:h|hs|horas?)?\b", normalized)
    if not time_match:
        time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", normalized)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "00")
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}", None

    if "manhã" in normalized or "manha" in normalized:
        return None, "morning"
    if "tarde" in normalized:
        return None, "afternoon"
    if "noite" in normalized:
        return None, "evening"

    return None, None


def _format_slots_naturally(slots: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for slot in slots:
        starts_at = datetime.fromisoformat(str(slot["starts_at"]))
        labels.append(starts_at.strftime("%d/%m às %H:%M"))
    return ", ".join(labels)


def _summary_for_booking(
    *,
    contact_id: str,
    contact_name: str | None,
    service_name: str | None,
    request_summary: str,
) -> str:
    customer_label = contact_name or contact_id
    service_label = service_name or "Atendimento"
    return f"{service_label} | Cliente: {customer_label} | Contexto: {request_summary}"


def _description_for_booking(
    *,
    contact_id: str,
    contact_name: str | None,
    service_name: str | None,
    request_summary: str,
) -> str:
    return (
        f"Cliente: {contact_name or 'Não informado'}\n"
        f"Contato: {contact_id}\n"
        f"Serviço: {service_name or 'Atendimento'}\n"
        f"Resumo: {request_summary}"
    )


def _is_confirmation(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in CONFIRMATION_TERMS)


def _is_cancellation(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in CANCELLATION_TERMS)


def _mentions_scheduling(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in SCHEDULING_TERMS)


def _filter_slots_by_period(slots: list[dict[str, Any]], period: str | None) -> list[dict[str, Any]]:
    if not period:
        return slots

    def matches(hour: int) -> bool:
        if period == "morning":
            return 6 <= hour < 12
        if period == "afternoon":
            return 12 <= hour < 18
        if period == "evening":
            return 18 <= hour <= 22
        return True

    filtered: list[dict[str, Any]] = []
    for slot in slots:
        starts_at = datetime.fromisoformat(str(slot["starts_at"]))
        if matches(starts_at.hour):
            filtered.append(slot)
    return filtered


def _build_scheduling_reply_prefix(service_name: str | None) -> str:
    if service_name:
        return f"Perfeito. Vamos cuidar do agendamento de {service_name}."
    return "Perfeito. Vamos cuidar do seu agendamento."


def _close_active_flow_if_needed(flow: dict[str, Any] | None, *, status: str, state: dict[str, Any] | None = None) -> None:
    if flow:
        repository.close_conversation_flow(int(flow["id"]), status=status, state=state)


def _continue_scheduling_flow(
    *,
    flow: dict[str, Any] | None,
    contact_id: str,
    contact_name: str | None,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    state = dict(flow.get("state") if flow else {})
    service_name = state.get("service_name") or _extract_service_name(company_context, message)
    request_summary = state.get("request_summary") or _extract_request_summary(message)
    requested_date = state.get("requested_date")
    requested_time = state.get("requested_time")
    requested_period = state.get("requested_period")
    selected_slot = state.get("selected_slot")
    duration_minutes = int(state.get("duration_minutes") or _extract_slot_duration_minutes(company_context))
    minimum_notice_minutes = _extract_minimum_notice_minutes(company_context)

    parsed_date = _parse_natural_date(message, timezone_obj)
    parsed_time, parsed_period = _parse_natural_time(message)
    if parsed_date:
        requested_date = parsed_date.isoformat()
    if parsed_time:
        requested_time = parsed_time
    if parsed_period:
        requested_period = parsed_period

    if _is_cancellation(message):
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Tudo bem. Não vou seguir com o agendamento agora. Se quiser retomar depois, é só me chamar.",
            workflow_type="scheduling",
        )

    if selected_slot and _is_confirmation(message):
        booking = create_calendar_booking(
            company_id=company_id,
            contact_id=contact_id,
            title=_summary_for_booking(
                contact_id=contact_id,
                contact_name=contact_name,
                service_name=service_name,
                request_summary=request_summary,
            ),
            starts_at=str(selected_slot["starts_at"]),
            ends_at=str(selected_slot["ends_at"]),
            timezone_name=str(selected_slot["timezone"]),
            description=_description_for_booking(
                contact_id=contact_id,
                contact_name=contact_name,
                service_name=service_name,
                request_summary=request_summary,
            ),
        )
        state["booking_id"] = booking["id"]
        _close_active_flow_if_needed(flow, status="completed", state=state)
        starts_at = datetime.fromisoformat(str(booking["starts_at"]))
        human_time = starts_at.astimezone(timezone_obj).strftime("%d/%m às %H:%M")
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                f"Agendamento confirmado para {human_time}. "
                "Já deixei tudo registrado com o resumo do atendimento para a equipe seguir com contexto."
            ),
            action="schedule_confirmed",
            workflow_type="scheduling",
            metadata={"booking_id": booking["id"]},
        )

    if not service_name:
        state.update(
            {
                "service_name": None,
                "request_summary": request_summary,
                "duration_minutes": duration_minutes,
            }
        )
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="scheduling",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                "Consigo verificar isso para você. "
                "Me diz qual serviço ou tipo de atendimento você quer agendar para eu te oferecer os horários certos."
            ),
            workflow_type="scheduling",
        )

    if not requested_date:
        state.update(
            {
                "service_name": service_name,
                "request_summary": request_summary,
                "duration_minutes": duration_minutes,
            }
        )
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="scheduling",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                f"{_build_scheduling_reply_prefix(service_name)} "
                "Qual dia fica melhor para você? Se preferir, pode me dizer algo como amanhã, sexta ou uma data específica."
            ),
            workflow_type="scheduling",
        )

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=requested_date,
        end_date=requested_date,
        duration_minutes=duration_minutes,
        limit=8,
    )
    slots = _filter_slots_by_period(list(availability["slots"]), requested_period)

    earliest_allowed = datetime.now(timezone_obj) + timedelta(minutes=minimum_notice_minutes)
    slots = [
        slot
        for slot in slots
        if datetime.fromisoformat(str(slot["starts_at"])).astimezone(timezone_obj) >= earliest_allowed
    ]

    if requested_time:
        matching_slot = next(
            (
                slot
                for slot in slots
                if datetime.fromisoformat(str(slot["starts_at"])).astimezone(timezone_obj).strftime("%H:%M") == requested_time
            ),
            None,
        )
        if matching_slot:
            state.update(
                {
                    "service_name": service_name,
                    "request_summary": request_summary,
                    "requested_date": requested_date,
                    "requested_time": requested_time,
                    "requested_period": requested_period,
                    "duration_minutes": duration_minutes,
                    "selected_slot": matching_slot,
                }
            )
            repository.upsert_conversation_flow(
                contact_id=contact_id,
                company_id=company_id,
                flow_type="scheduling",
                state=state,
            )
            human_date = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=(
                    f"Tenho esse horário disponível para {human_date}. "
                    "Se estiver tudo certo, me responde com um sim que eu já confirmo para você."
                ),
                action="schedule_confirmation_requested",
                workflow_type="scheduling",
            )

    if not slots:
        state.update(
            {
                "service_name": service_name,
                "request_summary": request_summary,
                "requested_date": requested_date,
                "requested_time": None,
                "requested_period": requested_period,
                "duration_minutes": duration_minutes,
                "selected_slot": None,
            }
        )
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="scheduling",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                f"Para {requested_date} eu não encontrei um horário livre dentro das regras atuais. "
                "Se quiser, posso verificar outro dia ou outro período para te sugerir as melhores opções."
            ),
            workflow_type="scheduling",
        )

    selected_slot = slots[0]
    state.update(
        {
            "service_name": service_name,
            "request_summary": request_summary,
            "requested_date": requested_date,
            "requested_time": requested_time,
            "requested_period": requested_period,
            "duration_minutes": duration_minutes,
            "candidate_slots": slots[:3],
            "selected_slot": selected_slot,
        }
    )
    repository.upsert_conversation_flow(
        contact_id=contact_id,
        company_id=company_id,
        flow_type="scheduling",
        state=state,
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"{_build_scheduling_reply_prefix(service_name)} "
            f"Encontrei estes horários: {_format_slots_naturally(slots[:3])}. "
            "Se algum deles funcionar para você, me diz qual prefere ou responde com sim para eu confirmar a primeira opção."
        ),
        action="schedule_options_presented",
        workflow_type="scheduling",
    )


def handle_conversation_workflow(
    *,
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None,
    company_context: dict[str, Any],
) -> WorkflowResult:
    active_flow = repository.get_active_conversation_flow(contact_id=contact_id, company_id=company_id)
    active_flow_type = active_flow.get("flow_type") if active_flow else None
    normalized_message = _normalize_text(message)

    if active_flow_type == "scheduling":
        return _continue_scheduling_flow(
            flow=active_flow,
            contact_id=contact_id,
            contact_name=contact_name,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )

    if _scheduling_enabled(company_context) and _mentions_scheduling(normalized_message):
        return _continue_scheduling_flow(
            flow=None,
            contact_id=contact_id,
            contact_name=contact_name,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )

    return WorkflowResult(handled=False)
