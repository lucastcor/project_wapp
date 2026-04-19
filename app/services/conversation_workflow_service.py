import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.llm.providers.openai_provider import OpenAIProvider
from app.services.appointment_queue_service import process_queue_contact_message, release_booking_slot
from app.services.calendar_service import (
    create_calendar_booking,
    query_calendar_availability,
    resolve_scheduling_profile,
    reschedule_calendar_booking,
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
BOOKING_CHANGE_TERMS = {
    "cancelar meu agendamento",
    "cancelar meu horário",
    "cancelar meu horario",
    "cancelar consulta",
    "desmarcar",
    "remarcar",
    "reagendar",
    "mudar meu horário",
    "mudar meu horario",
    "trocar meu horário",
    "trocar meu horario",
}
OPERATOR_SCHEDULING_TERMS = {
    "marca o cliente",
    "marque o cliente",
    "agenda o cliente",
    "agende o cliente",
    "marca para",
    "agende para",
    "marque para",
    "encaixa",
    "encaixe",
}
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
BOOKING_CHANGE_INTENTS = {
    "cancel_booking",
    "reschedule_booking",
    "keep_booking",
    "unclear",
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


def _get_current_time(timezone_obj: ZoneInfo) -> datetime:
    return datetime.now(timezone_obj)


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


def _extract_minimum_notice_minutes(
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None = None,
) -> int:
    configured = (scheduling_profile or {}).get("minimum_notice_minutes")
    if isinstance(configured, int) and configured >= 0:
        return configured

    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("minimum_notice_minutes")
    if isinstance(configured, int) and configured >= 0:
        return configured

    window_text = str(company_context.get("business_hours", {}).get("scheduling_window") or "")
    match = re.search(r"(\d+)\s*hora", window_text.lower())
    if match:
        return int(match.group(1)) * 60
    return 120


def _extract_slot_duration_minutes(
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None = None,
) -> int:
    configured = (scheduling_profile or {}).get("default_duration_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured

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
    today = _get_current_time(timezone_obj).date()

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


def _is_operator_contact(company_context: dict[str, Any], contact_id: str) -> bool:
    internal_operation = company_context.get("internal_operation", {})
    if not isinstance(internal_operation, dict):
        return False
    configured_contacts = internal_operation.get("operator_contact_ids") or internal_operation.get("owner_contact_ids") or []
    if not isinstance(configured_contacts, list):
        return False
    normalized_contact_id = re.sub(r"\D+", "", contact_id or "")
    for configured_contact in configured_contacts:
        candidate_digits = re.sub(r"\D+", "", str(configured_contact or ""))
        if candidate_digits and candidate_digits == normalized_contact_id:
            return True
    return False


def _looks_like_booking_change_message(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in BOOKING_CHANGE_TERMS)


def _looks_like_operator_scheduling_message(message: str) -> bool:
    normalized = _normalize_text(message)
    if any(term in normalized for term in OPERATOR_SCHEDULING_TERMS):
        return True

    has_operator_verb = any(
        re.search(rf"\b{verb}\b", normalized)
        for verb in {"marca", "marcar", "marque", "agenda", "agendar", "agende", "encaixa", "encaixar", "encaixe"}
    )
    if not has_operator_verb:
        return False

    return (
        "cliente" in normalized
        or bool(re.search(r"\b(hoje|amanh[ãa]|segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)\b", normalized))
        or bool(re.search(r"\b\d{1,2}h(?:\d{2})?\b|\b\d{1,2}:\d{2}\b", normalized))
    )


def _parse_choice_number(message: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\b", _normalize_text(message))
    if not match:
        return None
    return int(match.group(1))


def _format_booking_label(booking: dict[str, Any], timezone_obj: ZoneInfo) -> str:
    starts_at = datetime.fromisoformat(str(booking["starts_at"])).astimezone(timezone_obj)
    title = str(booking.get("title") or "Agendamento")
    return f"{title} em {starts_at.strftime('%d/%m às %H:%M')}"


def _get_upcoming_bookings(
    *,
    contact_id: str,
    company_id: int | None,
    timezone_obj: ZoneInfo,
) -> list[dict[str, Any]]:
    reference_time = _get_current_time(timezone_obj)
    bookings = repository.get_appointment_bookings(company_id=company_id, contact_id=contact_id)
    return [
        booking
        for booking in bookings
        if str(booking.get("status") or "") in {"confirmed", "pending"}
        and datetime.fromisoformat(str(booking["starts_at"])).astimezone(timezone_obj) >= reference_time - timedelta(hours=2)
    ]


def _select_booking_from_message(
    *,
    bookings: list[dict[str, Any]],
    message: str,
    timezone_obj: ZoneInfo,
) -> dict[str, Any] | None:
    if not bookings:
        return None
    if len(bookings) == 1:
        return bookings[0]

    selected_date = _parse_natural_date(message, timezone_obj)
    if selected_date:
        matching = [
            booking
            for booking in bookings
            if datetime.fromisoformat(str(booking["starts_at"])).astimezone(timezone_obj).date() == selected_date
        ]
        if len(matching) == 1:
            return matching[0]

    choice_number = _parse_choice_number(message)
    if choice_number and 1 <= choice_number <= len(bookings):
        return bookings[choice_number - 1]

    return None


def _fallback_booking_change_intent(message: str) -> dict[str, Any]:
    normalized = _normalize_text(message)
    if any(term in normalized for term in {"remarcar", "reagendar", "mudar meu horário", "mudar meu horario", "trocar meu horário", "trocar meu horario"}):
        intent = "reschedule_booking"
    elif any(term in normalized for term in {"cancelar", "desmarcar", "não vou conseguir", "nao vou conseguir"}):
        intent = "cancel_booking"
    elif any(term in normalized for term in {"manter", "deixa como está", "deixa como esta"}):
        intent = "keep_booking"
    else:
        intent = "unclear"

    return {
        "intent": intent,
        "confidence": 0.55 if intent != "unclear" else 0.2,
        "reason": "Fallback heurístico para alteração de booking.",
    }


def _interpret_booking_change_intent(
    *,
    message: str,
    bookings: list[dict[str, Any]],
    timezone_obj: ZoneInfo,
) -> dict[str, Any]:
    booking_summaries = [_format_booking_label(booking, timezone_obj) for booking in bookings]
    if settings.openai_api_key:
        try:
            provider = OpenAIProvider()
            result = provider.interpret_booking_change_request(
                customer_message=message,
                booking_summaries=booking_summaries,
            )
            if str(result.get("intent") or "").strip().lower() in BOOKING_CHANGE_INTENTS:
                return result
        except Exception:
            pass
    return _fallback_booking_change_intent(message)


def _fallback_operator_booking_command(message: str) -> dict[str, Any]:
    normalized = _normalize_text(message)
    customer_reference = None
    customer_match = re.search(r"cliente\s+(.+?)(?:\s+(?:para|pra|amanhã|amanha|hoje|segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)\b|$)", normalized)
    if customer_match:
        customer_reference = customer_match.group(1).strip(" ,.-")
    return {
        "intent": "schedule_for_customer" if customer_reference else "unclear",
        "customer_reference": customer_reference,
        "service_name": None,
        "customer_profile": None,
        "reason": "Fallback heurístico para comando interno de agenda.",
    }


def _extract_operator_booking_command(message: str) -> dict[str, Any]:
    if settings.openai_api_key:
        try:
            provider = OpenAIProvider()
            result = provider.extract_operator_booking_command(operator_message=message)
            if str(result.get("intent") or "").strip().lower() in {"schedule_for_customer", "unclear"}:
                return result
        except Exception:
            pass
    return _fallback_operator_booking_command(message)


def _build_booking_change_selection_reply(bookings: list[dict[str, Any]], timezone_obj: ZoneInfo) -> str:
    parts = [
        f"{index}. {_format_booking_label(booking, timezone_obj)}"
        for index, booking in enumerate(bookings, start=1)
    ]
    return (
        "Encontrei mais de um agendamento seu. Me diz qual deles você quer alterar:\n"
        + "\n".join(parts)
    )


def _build_operator_contact_selection_reply(matches: list[dict[str, Any]]) -> str:
    parts = []
    for index, contact in enumerate(matches, start=1):
        label = contact.get("contact_name") or contact.get("contact_id")
        parts.append(f"{index}. {label} | {contact.get('contact_id')}")
    return (
        "Encontrei mais de um contato com essa referência. Me confirma qual é o cliente certo:\n"
        + "\n".join(parts)
    )


def _get_booking_duration_minutes(booking: dict[str, Any]) -> int:
    starts_at = datetime.fromisoformat(str(booking["starts_at"]))
    ends_at = datetime.fromisoformat(str(booking["ends_at"]))
    return max(15, int((ends_at - starts_at).total_seconds() / 60))


def _resolve_contact_choice(
    *,
    message: str,
    matches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not matches:
        return None
    if len(matches) == 1:
        only_match = matches[0]
        if _is_confirmation(message):
            return only_match

        choice_number = _parse_choice_number(message)
        if choice_number == 1:
            return only_match

        normalized_digits = re.sub(r"\D+", "", message or "")
        contact_digits = re.sub(r"\D+", "", str(only_match.get("contact_id") or ""))
        if normalized_digits and contact_digits and normalized_digits == contact_digits:
            return only_match

        normalized_message = _normalize_text(message)
        candidate_name = _normalize_text(str(only_match.get("contact_name") or ""))
        if candidate_name and normalized_message == candidate_name:
            return only_match
        return None

    choice_number = _parse_choice_number(message)
    if choice_number and 1 <= choice_number <= len(matches):
        return matches[choice_number - 1]

    normalized_digits = re.sub(r"\D+", "", message or "")
    if normalized_digits:
        for contact in matches:
            contact_digits = re.sub(r"\D+", "", str(contact.get("contact_id") or ""))
            if contact_digits and normalized_digits in contact_digits:
                return contact

    normalized_message = _normalize_text(message)
    for contact in matches:
        candidate_name = _normalize_text(str(contact.get("contact_name") or ""))
        if candidate_name and normalized_message == candidate_name:
            return contact
    return None


def _find_contact_matches(
    *,
    company_id: int | None,
    customer_reference: str,
) -> list[dict[str, Any]]:
    matches = repository.search_contacts(
        query=customer_reference,
        company_id=company_id,
        limit=10,
    )
    return matches


def _booking_change_result_metadata(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "booking_id": booking.get("id"),
        "contact_id": booking.get("contact_id"),
        "starts_at": booking.get("starts_at"),
        "ends_at": booking.get("ends_at"),
    }


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
    customer_profile = state.get("customer_profile")
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        requested_profile_key=state.get("scheduling_profile_key"),
        service_name=service_name,
        customer_profile=str(customer_profile) if customer_profile is not None else None,
        request_context=f"{request_summary} {message}",
    )
    scheduling_profile_key = scheduling_profile.get("key") if scheduling_profile else state.get("scheduling_profile_key")
    duration_minutes = int(
        state.get("duration_minutes") or _extract_slot_duration_minutes(company_context, scheduling_profile)
    )
    minimum_notice_minutes = _extract_minimum_notice_minutes(company_context, scheduling_profile)

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
            scheduling_profile_key=str(scheduling_profile_key) if scheduling_profile_key else None,
            customer_profile=str(customer_profile) if customer_profile is not None else None,
            service_name=service_name,
            request_context=request_summary,
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
                "scheduling_profile_key": scheduling_profile_key,
                "customer_profile": customer_profile,
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
                "scheduling_profile_key": scheduling_profile_key,
                "customer_profile": customer_profile,
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
        scheduling_profile_key=str(scheduling_profile_key) if scheduling_profile_key else None,
        customer_profile=str(customer_profile) if customer_profile is not None else None,
        service_name=service_name,
        request_context=request_summary,
    )
    slots = _filter_slots_by_period(list(availability["slots"]), requested_period)

    earliest_allowed = _get_current_time(timezone_obj) + timedelta(minutes=minimum_notice_minutes)
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
                "scheduling_profile_key": scheduling_profile_key,
                "customer_profile": customer_profile,
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
                "scheduling_profile_key": scheduling_profile_key,
                "customer_profile": customer_profile,
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
            "scheduling_profile_key": scheduling_profile_key,
            "customer_profile": customer_profile,
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


def _continue_booking_change_flow(
    *,
    flow: dict[str, Any] | None,
    contact_id: str,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    state = dict(flow.get("state") if flow else {})
    upcoming_bookings = _get_upcoming_bookings(
        contact_id=contact_id,
        company_id=company_id,
        timezone_obj=timezone_obj,
    )

    if not upcoming_bookings:
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Eu não encontrei um agendamento ativo seu para alterar agora. Se quiser, posso te ajudar a marcar um novo horário.",
            workflow_type="booking_change",
            action="booking_change_not_found",
        )

    action = state.get("action")
    if not action:
        interpreted = _interpret_booking_change_intent(
            message=message,
            bookings=upcoming_bookings,
            timezone_obj=timezone_obj,
        )
        action = interpreted["intent"]
        state["action"] = action

    selected_booking = None
    if state.get("booking_id"):
        selected_booking = next(
            (booking for booking in upcoming_bookings if int(booking["id"]) == int(state["booking_id"])),
            None,
        )
    if not selected_booking:
        selected_booking = _select_booking_from_message(
            bookings=upcoming_bookings,
            message=message,
            timezone_obj=timezone_obj,
        )
        if selected_booking:
            state["booking_id"] = int(selected_booking["id"])

    if action == "keep_booking":
        _close_active_flow_if_needed(flow, status="completed", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Perfeito. Vou manter seu agendamento como está. Se quiser alterar depois, me chama por aqui.",
            workflow_type="booking_change",
            action="booking_change_kept",
        )

    if not selected_booking:
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=_build_booking_change_selection_reply(upcoming_bookings, timezone_obj),
            workflow_type="booking_change",
            action="booking_change_selection_requested",
            metadata={"booking_candidates": [_booking_change_result_metadata(booking) for booking in upcoming_bookings]},
        )

    state["booking_id"] = int(selected_booking["id"])
    booking_label = _format_booking_label(selected_booking, timezone_obj)

    if action == "cancel_booking":
        if _is_confirmation(message) or state.get("awaiting_confirmation") is False:
            result = release_booking_slot(booking_id=int(selected_booking["id"]))
            _close_active_flow_if_needed(flow, status="completed", state=state)
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=f"Pronto. Cancelei {booking_label}. Se quiser, eu também posso te ajudar a encontrar outra data.",
                workflow_type="booking_change",
                action="booking_cancelled",
                metadata={
                    "booking": result.get("booking"),
                    "queue_recovery": result.get("queue_recovery"),
                },
            )

        state["awaiting_confirmation"] = True
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"Posso cancelar {booking_label}. Se quiser seguir, me responde com um sim.",
            workflow_type="booking_change",
            action="booking_cancel_confirmation_requested",
            metadata=_booking_change_result_metadata(selected_booking),
        )

    if action != "reschedule_booking":
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Entendi que você quer alterar um agendamento, mas ainda não consegui definir se é cancelamento ou remarcação. Me diz se você quer cancelar ou remarcar.",
            workflow_type="booking_change",
            action="booking_change_clarification_requested",
        )

    parsed_date = _parse_natural_date(message, timezone_obj)
    parsed_time, parsed_period = _parse_natural_time(message)
    if parsed_date:
        state["requested_date"] = parsed_date.isoformat()
    if parsed_time:
        state["requested_time"] = parsed_time
    if parsed_period:
        state["requested_period"] = parsed_period

    selected_slot = state.get("selected_slot")
    if selected_slot and _is_confirmation(message):
        result = reschedule_calendar_booking(
            booking_id=int(selected_booking["id"]),
            starts_at=str(selected_slot["starts_at"]),
            ends_at=str(selected_slot["ends_at"]),
            timezone_name=str(selected_slot["timezone"]),
            scheduling_profile_key=str(selected_booking.get("scheduling_profile_key") or "").strip() or None,
            customer_profile=str(selected_booking.get("customer_profile")) if selected_booking.get("customer_profile") is not None else None,
        )
        _close_active_flow_if_needed(flow, status="completed", state=state)
        starts_at = datetime.fromisoformat(str(result["starts_at"])).astimezone(timezone_obj)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"Perfeito. Remarquei seu atendimento para {starts_at.strftime('%d/%m às %H:%M')}.",
            workflow_type="booking_change",
            action="booking_rescheduled",
            metadata={"booking_id": result["id"]},
        )

    if not state.get("requested_date"):
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"Claro. Para remarcar {booking_label}, me diz qual novo dia ou período funciona melhor para você.",
            workflow_type="booking_change",
            action="booking_reschedule_date_requested",
            metadata=_booking_change_result_metadata(selected_booking),
        )

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=str(state["requested_date"]),
        end_date=str(state["requested_date"]),
        duration_minutes=_get_booking_duration_minutes(selected_booking),
        limit=6,
        scheduling_profile_key=str(selected_booking.get("scheduling_profile_key") or "").strip() or None,
        customer_profile=str(selected_booking.get("customer_profile")) if selected_booking.get("customer_profile") is not None else None,
        request_context=str(selected_booking.get("description") or selected_booking.get("title") or ""),
        ignore_booking_id=int(selected_booking["id"]),
    )
    slots = _filter_slots_by_period(list(availability["slots"]), state.get("requested_period"))

    requested_time = state.get("requested_time")
    if requested_time:
        matching_slot = next(
            (
                slot
                for slot in slots
                if datetime.fromisoformat(str(slot["starts_at"])).astimezone(timezone_obj).strftime("%H:%M") == str(requested_time)
            ),
            None,
        )
        if matching_slot:
            state["selected_slot"] = matching_slot
            repository.upsert_conversation_flow(
                contact_id=contact_id,
                company_id=company_id,
                flow_type="booking_change",
                state=state,
            )
            human_date = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=f"Tenho esse novo horário disponível: {human_date}. Se quiser, me responde com um sim que eu já remarco para você.",
                workflow_type="booking_change",
                action="booking_reschedule_confirmation_requested",
                metadata={"booking_id": selected_booking["id"]},
            )

    if not slots:
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Para esse dia eu não encontrei horário livre dentro das regras atuais. Se quiser, me fala outro dia ou outro período e eu continuo buscando.",
            workflow_type="booking_change",
            action="booking_reschedule_no_slots",
        )

    state["candidate_slots"] = slots[:3]
    state["selected_slot"] = slots[0]
    repository.upsert_conversation_flow(
        contact_id=contact_id,
        company_id=company_id,
        flow_type="booking_change",
        state=state,
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"Para remarcar {booking_label}, encontrei estas opções: {_format_slots_naturally(slots[:3])}. "
            "Se quiser, responde com sim para eu confirmar a primeira opção ou me diz qual prefere."
        ),
        workflow_type="booking_change",
        action="booking_reschedule_options_presented",
        metadata={"booking_id": selected_booking["id"]},
    )


def _continue_operator_booking_flow(
    *,
    flow: dict[str, Any] | None,
    operator_contact_id: str,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    state = dict(flow.get("state") if flow else {})

    if not flow:
        command = _extract_operator_booking_command(message)
        if command.get("intent") != "schedule_for_customer":
            return WorkflowResult(handled=False)
        state.update(
            {
                "action": "schedule_for_customer",
                "customer_reference": command.get("customer_reference"),
                "service_name": command.get("service_name") or _extract_service_name(company_context, message),
                "customer_profile": command.get("customer_profile"),
                "request_summary": _extract_request_summary(message),
            }
        )
        parsed_date = _parse_natural_date(message, timezone_obj)
        parsed_time, parsed_period = _parse_natural_time(message)
        if parsed_date:
            state["requested_date"] = parsed_date.isoformat()
        if parsed_time:
            state["requested_time"] = parsed_time
        if parsed_period:
            state["requested_period"] = parsed_period

    customer_reference = state.get("customer_reference")
    if not customer_reference:
        repository.upsert_conversation_flow(
            contact_id=operator_contact_id,
            company_id=company_id,
            flow_type="operator_booking",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Me diz qual cliente você quer agendar. Pode ser o nome completo ou o número.",
            workflow_type="operator_booking",
            action="operator_customer_reference_requested",
        )

    matches = state.get("matched_contacts")
    if not matches:
        resolved_matches = _find_contact_matches(
            company_id=company_id,
            customer_reference=str(customer_reference),
        )
        matches = [
            {
                "contact_id": match.get("contact_id"),
                "contact_name": match.get("contact_name"),
                "customer_profile": match.get("customer_profile"),
            }
            for match in resolved_matches
        ]
        state["matched_contacts"] = matches

    if not matches:
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Não encontrei esse cliente na base atual. Se quiser, me manda o nome completo ou o telefone.",
            workflow_type="operator_booking",
            action="operator_customer_not_found",
        )

    selected_contact = None
    if state.get("selected_contact_id"):
        selected_contact = next(
            (item for item in matches if item.get("contact_id") == state.get("selected_contact_id")),
            None,
        )
    if not selected_contact:
        selected_contact = _resolve_contact_choice(message=message, matches=matches)
        if selected_contact:
            state["selected_contact_id"] = selected_contact.get("contact_id")

    if not selected_contact:
        repository.upsert_conversation_flow(
            contact_id=operator_contact_id,
            company_id=company_id,
            flow_type="operator_booking",
            state=state,
        )
        if len(matches) == 1:
            only_match = matches[0]
            label = only_match.get("contact_name") or only_match.get("contact_id")
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=f"Encontrei {label} | {only_match.get('contact_id')}. É esse contato mesmo? Se sim, me responde com um sim.",
                workflow_type="operator_booking",
                action="operator_customer_confirmation_requested",
            )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=_build_operator_contact_selection_reply(matches),
            workflow_type="operator_booking",
            action="operator_customer_selection_requested",
        )

    state["selected_contact_id"] = selected_contact.get("contact_id")
    if state.get("customer_profile") is None and selected_contact.get("customer_profile") is not None:
        state["customer_profile"] = selected_contact.get("customer_profile")

    if not state.get("requested_date"):
        repository.upsert_conversation_flow(
            contact_id=operator_contact_id,
            company_id=company_id,
            flow_type="operator_booking",
            state=state,
        )
        label = selected_contact.get("contact_name") or selected_contact.get("contact_id")
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"Contato confirmado: {label} | {selected_contact.get('contact_id')}. Agora me diz o dia ou período em que você quer encaixar esse cliente.",
            workflow_type="operator_booking",
            action="operator_booking_date_requested",
        )

    parsed_date = _parse_natural_date(message, timezone_obj)
    parsed_time, parsed_period = _parse_natural_time(message)
    if parsed_date:
        state["requested_date"] = parsed_date.isoformat()
    if parsed_time:
        state["requested_time"] = parsed_time
    if parsed_period:
        state["requested_period"] = parsed_period

    selected_slot = state.get("selected_slot")
    service_name = state.get("service_name")
    customer_profile = state.get("customer_profile")
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        service_name=str(service_name) if service_name else None,
        customer_profile=str(customer_profile) if customer_profile else None,
        request_context=str(state.get("request_summary") or ""),
    )
    duration_minutes = _extract_slot_duration_minutes(company_context, scheduling_profile)

    if selected_slot and _is_confirmation(message):
        target_contact_name = selected_contact.get("contact_name")
        booking = create_calendar_booking(
            company_id=company_id,
            contact_id=str(selected_contact["contact_id"]),
            title=_summary_for_booking(
                contact_id=str(selected_contact["contact_id"]),
                contact_name=str(target_contact_name) if target_contact_name else None,
                service_name=str(service_name) if service_name else None,
                request_summary=str(state.get("request_summary") or "Agendamento solicitado pelo operador."),
            ),
            starts_at=str(selected_slot["starts_at"]),
            ends_at=str(selected_slot["ends_at"]),
            timezone_name=str(selected_slot["timezone"]),
            description=_description_for_booking(
                contact_id=str(selected_contact["contact_id"]),
                contact_name=str(target_contact_name) if target_contact_name else None,
                service_name=str(service_name) if service_name else None,
                request_summary=f"Agendamento criado pelo operador {operator_contact_id}.",
            ),
            scheduling_profile_key=scheduling_profile.get("key") if scheduling_profile else None,
            customer_profile=str(customer_profile) if customer_profile else None,
            service_name=str(service_name) if service_name else None,
            request_context=str(state.get("request_summary") or ""),
        )
        _close_active_flow_if_needed(flow, status="completed", state=state)
        starts_at = datetime.fromisoformat(str(booking["starts_at"])).astimezone(timezone_obj)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                f"Perfeito. Agendei {selected_contact.get('contact_name') or selected_contact.get('contact_id')} "
                f"para {starts_at.strftime('%d/%m às %H:%M')}."
            ),
            workflow_type="operator_booking",
            action="operator_booking_created",
            metadata={"booking_id": booking["id"], "contact_id": selected_contact.get("contact_id")},
        )

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=str(state["requested_date"]),
        end_date=str(state["requested_date"]),
        duration_minutes=duration_minutes,
        limit=6,
        scheduling_profile_key=scheduling_profile.get("key") if scheduling_profile else None,
        customer_profile=str(customer_profile) if customer_profile else None,
        service_name=str(service_name) if service_name else None,
        request_context=str(state.get("request_summary") or ""),
    )
    slots = _filter_slots_by_period(list(availability["slots"]), state.get("requested_period"))
    requested_time = state.get("requested_time")
    if requested_time:
        matching_slot = next(
            (
                slot
                for slot in slots
                if datetime.fromisoformat(str(slot["starts_at"])).astimezone(timezone_obj).strftime("%H:%M") == str(requested_time)
            ),
            None,
        )
        if matching_slot:
            state["selected_slot"] = matching_slot
            repository.upsert_conversation_flow(
                contact_id=operator_contact_id,
                company_id=company_id,
                flow_type="operator_booking",
                state=state,
            )
            human_time = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=(
                    f"Contato confirmado: {selected_contact.get('contact_name') or selected_contact.get('contact_id')} | "
                    f"{selected_contact.get('contact_id')}. "
                    f"Tenho esse horário disponível: {human_time}. Se estiver certo, me responde com um sim."
                ),
                workflow_type="operator_booking",
                action="operator_booking_confirmation_requested",
            )

    if not slots:
        repository.upsert_conversation_flow(
            contact_id=operator_contact_id,
            company_id=company_id,
            flow_type="operator_booking",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Para esse dia eu não encontrei vaga disponível dentro das regras atuais. Se quiser, me passa outro dia ou período.",
            workflow_type="operator_booking",
            action="operator_booking_no_slots",
        )

    state["candidate_slots"] = slots[:3]
    state["selected_slot"] = slots[0]
    repository.upsert_conversation_flow(
        contact_id=operator_contact_id,
        company_id=company_id,
        flow_type="operator_booking",
        state=state,
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"Contato confirmado: {selected_contact.get('contact_name') or selected_contact.get('contact_id')} | {selected_contact.get('contact_id')}. "
            f"Encontrei estes horários: {_format_slots_naturally(slots[:3])}. "
            "Se quiser, responde com sim para eu confirmar a primeira opção ou me diz qual prefere."
        ),
        workflow_type="operator_booking",
        action="operator_booking_options_presented",
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

    queue_result = process_queue_contact_message(
        contact_id=contact_id,
        company_id=company_id,
        message=message,
    )
    if queue_result:
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=str(queue_result["reply"]),
            source="workflow",
            action=str(queue_result["action"]),
            priority="high",
            auto_reply=True,
            workflow_type=str(queue_result["workflow_type"]),
            metadata=queue_result.get("metadata"),
        )

    if active_flow_type == "scheduling":
        return _continue_scheduling_flow(
            flow=active_flow,
            contact_id=contact_id,
            contact_name=contact_name,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )

    if active_flow_type == "booking_change":
        return _continue_booking_change_flow(
            flow=active_flow,
            contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )

    if active_flow_type == "operator_booking":
        return _continue_operator_booking_flow(
            flow=active_flow,
            operator_contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )

    if _is_operator_contact(company_context, contact_id) and _looks_like_operator_scheduling_message(normalized_message):
        operator_result = _continue_operator_booking_flow(
            flow=None,
            operator_contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
        )
        if operator_result.handled:
            return operator_result

    if _scheduling_enabled(company_context) and _looks_like_booking_change_message(normalized_message):
        return _continue_booking_change_flow(
            flow=None,
            contact_id=contact_id,
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
