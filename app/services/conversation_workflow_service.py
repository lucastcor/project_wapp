import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.tenancy import require_company_id
from app.llm.factory import get_llm_provider
from app.llm.providers.openai_provider import OpenAIProvider
from app.services import conversation_control_service as control
from app.services.appointment_queue_service import (
    has_open_queue_offer,
    process_queue_contact_message,
    release_booking_slot,
)
from app.services.calendar_service import (
    BookingConflictError,
    BookingPolicyError,
    create_calendar_booking,
    query_calendar_availability,
    reschedule_calendar_booking,
    resolve_scheduling_profile,
)
from app.services.company_policy_service import (
    BookingChangeDecision,
    BookingChangeRule,
    EffectivePolicy,
    build_effective_policy,
    evaluate_booking_change,
    is_plain_greeting,
)
from app.services.execution_lease import assert_lease_before_effect
from app.storage import operation_repository as operations
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
SCHEDULING_TERMS = [
    "agendar",
    "marcar horário",
    "marcar horario",
    "marcar um horário",
    "marcar um horario",
    "horário disponível",
    "horario disponivel",
    "horários disponíveis",
    "horarios disponiveis",
    "disponibilidade",
    "tem horário",
    "tem horario",
    "tem vaga",
]
SCHEDULING_SERVICE_TERMS = ("agendamento", "consulta", "reunião", "reuniao")
SCHEDULING_DESIRE_TERMS = ("quero", "preciso", "gostaria", "pretendo")
CATALOG_QUESTION_TERMS = (
    "quanto custa",
    "qual o preço",
    "qual o preco",
    "qual o valor",
    "quais serviços",
    "quais servicos",
    "que serviços",
    "que servicos",
    "vocês oferecem",
    "voces oferecem",
)
BOOKING_CHANGE_TERMS = {
    "cancelar meu agendamento",
    "cancelar meu horário",
    "cancelar meu horario",
    "cancela meu agendamento",
    "cancela meu horário",
    "cancela meu horario",
    "cancele meu agendamento",
    "cancele meu horário",
    "cancele meu horario",
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
_VERB_PAST = {"create": "agendei", "cancel": "cancelei", "reschedule": "remarquei"}
# Consulta o dia inteiro: horário/período pedido é filtrado depois da consulta.
# Com limites de 6-8 vagas, "às 16h" ou "de tarde" caíam fora do recorte e o
# fluxo propunha silenciosamente a primeira vaga da manhã. A resposta segue
# mostrando só as três primeiras opções.
AVAILABILITY_QUERY_LIMIT = 96


TAP_CONTENT_TYPES = frozenset({"button", "interactive"})


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
    # Pedido de pendência para a equipe (ex.: cancelamento fora da política).
    # O orquestrador cria a pendência quando ambos vêm preenchidos.
    handoff_question_type: str | None = None
    handoff_question: str | None = None


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _get_current_time(timezone_obj: ZoneInfo) -> datetime:
    return datetime.now(timezone_obj)


def _now_utc(timezone_obj: ZoneInfo) -> datetime:
    return _get_current_time(timezone_obj).astimezone(UTC)


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


def _catalog_service_names(company_context: dict[str, Any]) -> list[str]:
    catalog = company_context.get("catalog", {})
    items = catalog.get("items", []) if isinstance(catalog, dict) else []
    return [
        str(item.get("name")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


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

    # Palavra inteira: "amanhã" contém "manhã", e "boa tarde" é saudação, não período.
    period_text = re.sub(r"\b(?:bom dia|boa tarde|boa noite)\b", " ", normalized)
    if re.search(r"\bmanh[ãa]\b", period_text):
        return None, "morning"
    if re.search(r"\btarde\b", period_text):
        return None, "afternoon"
    if re.search(r"\bnoite\b", period_text):
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


def _mentions_scheduling(message: str) -> bool:
    normalized = _normalize_text(message)
    if any(term in normalized for term in SCHEDULING_TERMS):
        return True
    # O nome de um serviço não basta para abrir um fluxo de agenda. Perguntas
    # como "quanto custa a consulta?" devem receber a informação pedida, sem
    # transformar interesse comercial em intenção de reservar.
    if any(term in normalized for term in CATALOG_QUESTION_TERMS):
        return False
    return any(term in normalized for term in SCHEDULING_SERVICE_TERMS) and any(
        term in normalized for term in SCHEDULING_DESIRE_TERMS
    )


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
        for verb in ("marca", "marcar", "marque", "agenda", "agendar", "agende", "encaixa", "encaixar", "encaixe")
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


def _parse_moment(value: Any, timezone_name: Any = None) -> datetime:
    """Datetime com timezone a partir de ISO (SQLite) ou datetime (PostgreSQL)."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(str(timezone_name or settings.calendar_timezone)))
    return parsed


def _utc_iso(value: Any, timezone_name: Any = None) -> str:
    return _parse_moment(value, timezone_name).astimezone(UTC).isoformat()


def _human_time(value: Any, timezone_obj: ZoneInfo, timezone_name: Any = None) -> str:
    return _parse_moment(value, timezone_name).astimezone(timezone_obj).strftime("%d/%m às %H:%M")


def _format_booking_label(booking: dict[str, Any], timezone_obj: ZoneInfo) -> str:
    starts_at = _parse_moment(booking["starts_at"], booking.get("timezone")).astimezone(timezone_obj)
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
        and _parse_moment(booking["starts_at"], booking.get("timezone")).astimezone(timezone_obj)
        >= reference_time - timedelta(hours=2)
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
            if _parse_moment(booking["starts_at"], booking.get("timezone")).astimezone(timezone_obj).date()
            == selected_date
        ]
        if len(matching) == 1:
            return matching[0]

    choice_number = _parse_choice_number(message)
    if choice_number and 1 <= choice_number <= len(bookings):
        return bookings[choice_number - 1]

    return None


def _fallback_booking_change_intent(message: str) -> dict[str, Any]:
    normalized = _normalize_text(message)
    if any(term in normalized for term in ("remarcar", "reagendar", "mudar meu horário", "mudar meu horario", "trocar meu horário", "trocar meu horario")):
        intent = "reschedule_booking"
    elif any(term in normalized for term in ("cancelar", "cancela", "cancele", "desmarcar", "não vou conseguir", "nao vou conseguir")):
        intent = "cancel_booking"
    elif any(term in normalized for term in ("manter", "deixa como está", "deixa como esta")):
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
    # Sugestão do modelo só escolhe o tipo do pedido; nunca autoriza escrita:
    # toda ação passa por proposta e aceite validados pelo serviço.
    booking_summaries = [_format_booking_label(booking, timezone_obj) for booking in bookings]
    if settings.classification_provider in {"openai", "ollama"}:
        try:
            provider = get_llm_provider(openai_factory=OpenAIProvider)
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
    if settings.classification_provider in {"openai", "ollama"}:
        try:
            provider = get_llm_provider(openai_factory=OpenAIProvider)
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
    starts_at = _parse_moment(booking["starts_at"], booking.get("timezone"))
    ends_at = _parse_moment(booking["ends_at"], booking.get("timezone"))
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
        if control.interpret_control_message(message).intent == control.CONFIRM:
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
    return repository.search_contacts(
        query=customer_reference,
        company_id=company_id,
        limit=10,
    )


def _booking_change_result_metadata(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "booking_id": booking.get("id"),
        "contact_id": booking.get("contact_id"),
        "starts_at": booking.get("starts_at"),
        "ends_at": booking.get("ends_at"),
    }


def _flow_state(flow: dict[str, Any] | None) -> dict[str, Any]:
    raw_state = flow.get("state") if flow else None
    return dict(raw_state) if isinstance(raw_state, dict) else {}


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


# ── proposta persistida, aceite e execução (CONTRATOS: "Operações e propostas") ──


@dataclass
class _Execution:
    # completed | replay | stale | not_executed | conflict | policy
    outcome: str
    operation: dict[str, Any] | None = None
    effect: Any = None
    detail: str | None = None
    policy_outcome: str | None = None
    policy_reason: str | None = None


def _resolve_policy(policy: EffectivePolicy | None, company_context: dict[str, Any]) -> EffectivePolicy:
    return policy if policy is not None else build_effective_policy(context=company_context, policies=None)


def _booking_version(booking: dict[str, Any]) -> str:
    """Versão do alvo: qualquer mudança de estado/horário invalida a proposta."""
    return "|".join(str(booking.get(key) or "") for key in ("status", "starts_at", "ends_at", "updated_at"))[:200]


def _proposal_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    proposal = state.get("proposal")
    if isinstance(proposal, dict) and proposal.get("operation_id") and proposal.get("params_hash"):
        return proposal
    return None


def _create_params(
    *,
    slot: dict[str, Any],
    service_name: Any,
    scheduling_profile_key: Any,
    customer_profile: Any,
    target_contact_id: str | None = None,
) -> dict[str, Any]:
    timezone_name = str(slot.get("timezone") or settings.calendar_timezone)
    params: dict[str, Any] = {
        "starts_at": _utc_iso(slot["starts_at"], timezone_name),
        "ends_at": _utc_iso(slot["ends_at"], timezone_name),
        "timezone": timezone_name,
        "service_name": str(service_name) if service_name else None,
        "scheduling_profile_key": str(scheduling_profile_key) if scheduling_profile_key else None,
        "customer_profile": str(customer_profile) if customer_profile is not None else None,
    }
    if target_contact_id:
        params["target_contact_id"] = target_contact_id
    return params


def _persist_proposal(
    *,
    flow: dict[str, Any] | None,
    contact_id: str,
    company_id: int | None,
    flow_type: str,
    state: dict[str, Any],
    kind: str,
    params: dict[str, Any],
    policy: EffectivePolicy,
    timezone_obj: ZoneInfo,
    label: str,
    booking: dict[str, Any] | None = None,
    source: str = "conversation",
) -> dict[str, Any]:
    """Cria a proposta antes de a confirmação ser apresentada ao cliente.

    A nova proposta substitui a anterior do mesmo contato na mesma transação.
    """
    cid = require_company_id(company_id, context="booking proposal")
    state.pop("proposal", None)
    if flow and flow.get("id"):
        flow_id = int(flow["id"])
    else:
        flow_id = int(
            repository.upsert_conversation_flow(
                contact_id=contact_id, company_id=company_id, flow_type=flow_type, state=state
            )
        )
    operation = operations.create_proposal(
        company_id=cid,
        contact_id=contact_id,
        kind=kind,
        params=params,
        ttl_minutes=policy.confirmation.proposal_ttl_minutes,
        booking_id=int(booking["id"]) if booking else None,
        target_version=_booking_version(booking) if booking else None,
        source=source,
        flow_id=flow_id,
        now=_now_utc(timezone_obj),
    )
    state["proposal"] = {
        "operation_id": int(operation["id"]),
        "params_hash": str(operation["params_hash"]),
        "kind": kind,
        "booking_id": int(booking["id"]) if booking else None,
        "label": label,
    }
    repository.upsert_conversation_flow(contact_id=contact_id, company_id=company_id, flow_type=flow_type, state=state)
    return operation


def _discard_proposal(
    *, company_id: int | None, contact_id: str, proposal: dict[str, Any] | None, timezone_obj: ZoneInfo
) -> None:
    if not proposal or company_id is None:
        return
    operations.reject_proposal(
        company_id=int(company_id),
        contact_id=contact_id,
        operation_id=int(proposal["operation_id"]),
        now=_now_utc(timezone_obj),
    )


def _business_error(error: BaseException) -> BookingConflictError | BookingPolicyError | None:
    # Só a cadeia explícita (`raise ... from`): `release_booking_slot` embrulha
    # erros da agenda em AppointmentQueueError preservando a causa.
    current: BaseException | None = error
    for _depth in range(5):
        if current is None:
            return None
        if isinstance(current, (BookingConflictError, BookingPolicyError)):
            return current
        current = current.__cause__
    return None


def _policy_error_code(reason: str | None) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(reason or ""))[:73]
    return f"policy_{sanitized or 'unknown'}"


def _operation_result(booking: dict[str, Any], timezone_name: Any = None) -> dict[str, Any]:
    """Resumo durável do efeito, sem texto do cliente."""
    zone = str(booking.get("timezone") or timezone_name or "") or None
    summary: dict[str, Any] = {
        "booking_id": int(booking["id"]),
        "status": booking.get("status"),
        "timezone": zone,
    }
    for key in ("starts_at", "ends_at"):
        if booking.get(key):
            summary[key] = _utc_iso(booking[key], zone)
    return summary


def _accept_and_execute(
    *,
    company_id: int,
    contact_id: str,
    proposal: dict[str, Any],
    message_ref: str | None,
    timezone_obj: ZoneInfo,
    effect: Callable[[dict[str, Any]], tuple[Any, dict[str, Any], int | None]],
    load_target: Callable[[], dict[str, Any] | None] | None = None,
    precheck: Callable[[dict[str, Any]], BookingChangeDecision] | None = None,
) -> _Execution:
    """Aceite vinculado à proposta → claim → revalidação do alvo → efeito → conclusão.

    Só `accepted` executa. Sucesso só é devolvido depois de `complete_operation`.
    Erro de negócio conhecido falha a operação; erro inesperado vai para revisão
    e é relançado (nunca vira sucesso falso).
    """
    # Fencing (P06, F03): quem perdeu o job de entrada não aceita nem executa.
    assert_lease_before_effect()
    now = _now_utc(timezone_obj)
    accepted = operations.accept_proposal(
        company_id=company_id,
        contact_id=contact_id,
        operation_id=int(proposal["operation_id"]),
        params_hash=str(proposal["params_hash"]),
        # API síncrona não tem id estável de mensagem: sem proteção de reentrega.
        confirmation_ref=message_ref or f"conversation:{uuid4().hex}",
        now=now,
    )
    if accepted.outcome == "replay":
        return _Execution("replay", operation=accepted.operation)
    if accepted.outcome != "accepted" or accepted.operation is None:
        return _Execution("stale", operation=accepted.operation, detail=accepted.outcome)

    operation_id = int(accepted.operation["id"])
    claimed = operations.claim_for_execution(company_id=company_id, operation_id=operation_id, now=now)
    if claimed is None:
        # Outro processamento da mesma confirmação já assumiu a execução.
        return _Execution("replay", operation=operations.get_operation(company_id=company_id, operation_id=operation_id))

    try:
        if load_target is not None:
            target = load_target()
            if target is None or _booking_version(target) != claimed.get("target_version"):
                operations.fail_operation(
                    company_id=company_id, operation_id=operation_id, error_code="target_changed", now=now
                )
                return _Execution("not_executed", operation=claimed, detail="target_changed")
            if precheck is not None:
                decision = precheck(target)
                if not decision.allowed:
                    raise BookingPolicyError(
                        "Booking change outside the published policy.",
                        outcome=decision.outcome,
                        reason=decision.reason,
                    )
        raw_effect, summary, booking_id = effect(dict(claimed.get("params") or {}))
    except Exception as error:
        business = _business_error(error)
        if isinstance(business, BookingConflictError):
            operations.fail_operation(
                company_id=company_id,
                operation_id=operation_id,
                error_code="slot_unavailable",
                now=_now_utc(timezone_obj),
            )
            return _Execution("conflict", operation=claimed)
        if isinstance(business, BookingPolicyError):
            operations.fail_operation(
                company_id=company_id,
                operation_id=operation_id,
                error_code=_policy_error_code(business.reason),
                now=_now_utc(timezone_obj),
            )
            return _Execution(
                "policy", operation=claimed, policy_outcome=business.outcome, policy_reason=business.reason
            )
        operations.mark_review(
            company_id=company_id,
            operation_id=operation_id,
            error_code="unexpected_error",
            now=_now_utc(timezone_obj),
        )
        raise

    if not operations.complete_operation(
        company_id=company_id,
        operation_id=operation_id,
        result=summary,
        booking_id=booking_id,
        now=_now_utc(timezone_obj),
    ):
        raise RuntimeError("Booking operation left the executing state before completion.")
    return _Execution("completed", operation=claimed, effect=raw_effect)


def _stale_prefix(detail: str | None, kind: str) -> str:
    verb = _VERB_PAST.get(kind, "alterei")
    if detail == "expired":
        return f"A confirmação anterior expirou, então não {verb} nada. "
    if detail == "target_changed":
        return f"Esse agendamento mudou desde a minha última mensagem, então não {verb} nada. "
    return f"Aquela confirmação não vale mais, então não {verb} nada. "


def _replay_result(operation: dict[str, Any] | None, timezone_obj: ZoneInfo) -> WorkflowResult:
    """Reentrega de uma confirmação já consumida: responde o que se sabe, sem executar."""
    operation = operation or {}
    status = str(operation.get("status") or "")
    kind = str(operation.get("kind") or "")
    result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
    params = operation.get("params") if isinstance(operation.get("params"), dict) else {}
    starts_at = (result or {}).get("starts_at") or (params or {}).get("starts_at")
    when = _human_time(starts_at, timezone_obj) if starts_at else None
    if status == "completed":
        if kind == "cancel":
            reply = "Esse cancelamento já foi concluído. Não precisa confirmar de novo."
        elif kind == "reschedule":
            reply = (
                f"Seu atendimento já está remarcado para {when}. Não precisa confirmar de novo."
                if when
                else "Essa remarcação já foi concluída. Não precisa confirmar de novo."
            )
        else:
            reply = (
                f"Esse agendamento já está confirmado para {when}. Não precisa confirmar de novo."
                if when
                else "Esse agendamento já está confirmado. Não precisa confirmar de novo."
            )
    elif status == "review":
        reply = "Já recebi sua confirmação e ela está em verificação pela nossa equipe. Não precisa enviar de novo."
    elif status == "failed":
        reply = (
            "Já recebi essa confirmação, mas a operação não foi concluída. "
            "Se quiser, posso verificar outras opções para você."
        )
    else:
        reply = "Já recebi sua confirmação e ela está em processamento. Não precisa enviar de novo."
    if operation.get("source") == "operator":
        workflow_type = "operator_booking"
    else:
        workflow_type = "scheduling" if kind == "create" else "booking_change"
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=reply,
        action="operation_replay",
        workflow_type=workflow_type,
        metadata={"operation_id": operation.get("id"), "operation_status": status or None},
    )


def _finish_replay(flow: dict[str, Any] | None, state: dict[str, Any], execution: _Execution, timezone_obj: ZoneInfo) -> WorkflowResult:
    if execution.operation and execution.operation.get("status") == "completed":
        _close_active_flow_if_needed(flow, status="completed", state=state)
    return _replay_result(execution.operation, timezone_obj)


def _operation_for_confirmation_ref(*, company_id: int, contact_id: str, message_ref: str) -> dict[str, Any] | None:
    for operation in operations.list_operations(company_id=company_id, contact_id=contact_id, limit=20):
        if operation.get("confirmation_ref") == message_ref:
            return operation
    return None


def _has_pending_confirmation(*, company_id: int | None, contact_id: str) -> bool:
    """Existe proposta de agenda ou oferta de encaixe que um "sim" confirmaria?"""
    if company_id is None:
        return True
    if operations.get_open_proposal(company_id=int(company_id), contact_id=contact_id) is not None:
        return True
    return has_open_queue_offer(contact_id=contact_id, company_id=company_id)


def _may_be_confirmation(message: str) -> bool:
    return any(
        control.interpret_control_message(message, kind=kind).intent == control.CONFIRM
        for kind in ("create", "cancel", "reschedule")
    )


def _exclude_slot(state: dict[str, Any], starts_at: Any) -> None:
    excluded = [str(item) for item in state.get("excluded_starts_at") or []]
    value = _utc_iso(starts_at)
    if value not in excluded:
        excluded.append(value)
    state["excluded_starts_at"] = excluded


def _without_excluded(slots: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = {str(item) for item in state.get("excluded_starts_at") or []}
    if not excluded:
        return slots
    return [slot for slot in slots if _utc_iso(slot["starts_at"], slot.get("timezone")) not in excluded]


def _apply_slot_change(
    state: dict[str, Any],
    change: control.ControlChange,
    current_slot: Any,
    timezone_obj: ZoneInfo,
    *,
    allow_service: bool,
) -> bool:
    """Aplica a mudança citada; devolve True quando a mensagem não deve ser reinterpretada."""
    if allow_service and change.services:
        state["service_name"] = change.services[0]
        state.pop("scheduling_profile_key", None)
        state.pop("duration_minutes", None)
    candidates = state.get("candidate_slots")
    if change.choice is not None and isinstance(candidates, list) and 1 <= change.choice <= len(candidates):
        chosen = _parse_moment(candidates[change.choice - 1]["starts_at"]).astimezone(timezone_obj)
        state["requested_date"] = chosen.date().isoformat()
        state["requested_time"] = chosen.strftime("%H:%M")
        return True
    if change.times:
        state["requested_time"] = change.times[0]
    elif change.other_option and not change.has_concrete_values and isinstance(current_slot, dict):
        _exclude_slot(state, current_slot["starts_at"])
        state["requested_time"] = None
    elif change.dates or change.periods:
        state["requested_time"] = None
    return False


def _change_rule(policy: EffectivePolicy, kind: str) -> BookingChangeRule:
    return policy.booking_rules.cancellation if kind == "cancel" else policy.booking_rules.reschedule


def _evaluate_change(
    policy: EffectivePolicy, kind: str, booking: dict[str, Any], timezone_obj: ZoneInfo
) -> BookingChangeDecision:
    return evaluate_booking_change(
        _change_rule(policy, kind),
        starts_at=_parse_moment(booking["starts_at"], booking.get("timezone")),
        now=_get_current_time(timezone_obj),
    )


def _human_minutes(minutes: int) -> str:
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"{days} dia" if days == 1 else f"{days} dias"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hora" if hours == 1 else f"{hours} horas"
    return f"{minutes} minutos"


def _policy_reason_text(reason: str | None, *, kind: str, policy: EffectivePolicy) -> str:
    if reason == "change_not_allowed":
        subject = {"cancel": "o cancelamento", "reschedule": "a remarcação"}.get(kind, "essa alteração")
        return f"{subject} não está disponível pelo atendimento automático"
    if reason == "booking_already_started":
        return "o horário já começou ou já passou"
    if reason == "inside_minimum_notice":
        if kind in {"cancel", "reschedule"} and _change_rule(policy, kind).min_notice_minutes > 0:
            notice = _human_minutes(_change_rule(policy, kind).min_notice_minutes)
            return f"é preciso pedir com pelo menos {notice} de antecedência"
        return "o prazo mínimo de antecedência já passou"
    return "o pedido está fora das regras da empresa"


def _policy_block_result(
    *,
    kind: str,
    label: str,
    booking_id: int | None,
    outcome: str | None,
    reason: str | None,
    policy: EffectivePolicy,
    flow: dict[str, Any] | None,
    state: dict[str, Any],
    workflow_type: str,
) -> WorkflowResult:
    state.pop("proposal", None)
    _close_active_flow_if_needed(flow, status="cancelled", state=state)
    noun = {"cancel": "cancelamento", "reschedule": "remarcação"}.get(kind, "agendamento")
    verb = {"cancel": "cancelar", "reschedule": "remarcar"}.get(kind, "agendar")
    reason_text = _policy_reason_text(reason, kind=kind, policy=policy)
    metadata = {"booking_id": booking_id, "policy_outcome": outcome, "policy_reason": reason}
    if outcome == "requires_human":
        target = f"agendamento #{booking_id}" if booking_id else "agendamento"
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                f"Não consigo concluir esse pedido de {noun} ({label}) pelo atendimento automático: {reason_text}. "
                "Já encaminhei para a nossa equipe, que continua com você por aqui. Nada foi alterado até lá."
            ),
            action="booking_change_requires_human",
            workflow_type=workflow_type,
            metadata=metadata,
            handoff_question_type="booking_policy_exception",
            handoff_question=(
                f"Pedido de {noun} do {target} ({label}) fora da política automática "
                f"(motivo: {reason or 'desconhecido'}). Avalie e retome o atendimento com o cliente."
            ),
        )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=f"Pela política da empresa, não consigo {verb} {label} por aqui: {reason_text}. Nada foi alterado.",
        action="booking_change_denied",
        workflow_type=workflow_type,
        metadata=metadata,
    )


# ── fluxo de nova reserva ─────────────────────────────────────────────


def _resume_scheduling_result(state: dict[str, Any]) -> WorkflowResult:
    """Saudação durante o fluxo: retoma o passo atual sem tocar no estado (C02)."""
    proposal = _proposal_from_state(state)
    candidates = state.get("candidate_slots")
    if proposal and isinstance(candidates, list) and len(candidates) > 1:
        step = (
            f"As opções que separei são: {_format_slots_naturally(candidates)}. "
            f"Me diz qual prefere ou responde com sim para eu confirmar {proposal.get('label')}."
        )
    elif proposal:
        step = (
            f"O horário de {proposal.get('label')} está aguardando sua confirmação. "
            "Se estiver tudo certo, me responde com um sim, ou me diz se prefere outro horário."
        )
    elif not state.get("service_name"):
        step = "Me diz qual serviço ou tipo de atendimento você quer agendar."
    elif not state.get("requested_date"):
        step = "Qual dia fica melhor para você?"
    else:
        step = "Me diz qual dia ou período prefere para eu continuar buscando horários."
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=f"Oi! Seguimos com o seu agendamento. {step}",
        action="scheduling_resumed",
        workflow_type="scheduling",
    )


def _abandon_scheduling_result(
    *,
    flow: dict[str, Any] | None,
    state: dict[str, Any],
    contact_id: str,
    company_id: int | None,
    timezone_obj: ZoneInfo,
) -> WorkflowResult:
    """Desistência da coleta (A04): encerra só este fluxo; reservas existentes ficam intactas."""
    state.pop("proposal", None)
    _close_active_flow_if_needed(flow, status="cancelled", state=state)
    reply = "Tudo bem. Não vou seguir com o agendamento agora. Se quiser retomar depois, é só me chamar."
    if _get_upcoming_bookings(contact_id=contact_id, company_id=company_id, timezone_obj=timezone_obj):
        reply += (
            " Seu agendamento já marcado continua mantido. Se você queria cancelar ou remarcar um horário já marcado, "
            'me diga "quero cancelar meu agendamento" ou "quero remarcar meu horário".'
        )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=reply,
        action="scheduling_abandoned",
        workflow_type="scheduling",
    )


def _scheduling_confirmation_reminder(state: dict[str, Any], proposal: dict[str, Any], intent: str) -> WorkflowResult:
    label = str(proposal.get("label") or "o horário proposto")
    service_name = state.get("service_name")
    target = f"o agendamento de {service_name} em {label}" if service_name else f"o agendamento em {label}"
    if intent == control.QUESTION:
        reply = (
            f"Ainda não confirmei nada. Essa confirmação vale só para {target} e não altera nenhum outro horário. "
            "Se estiver tudo certo, me responde com um sim; se preferir outro horário, é só me dizer."
        )
    else:
        reply = (
            f"Não consegui entender se posso confirmar, então ainda não confirmei nada. O horário separado é {label}. "
            "Me responde com um sim para confirmar ou me diz se prefere outro horário."
        )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=reply,
        action="schedule_confirmation_requested",
        workflow_type="scheduling",
        metadata={"operation_id": proposal.get("operation_id")},
    )


def _continue_scheduling_flow(
    *,
    flow: dict[str, Any] | None,
    contact_id: str,
    contact_name: str | None,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
    policy: EffectivePolicy | None = None,
    message_ref: str | None = None,
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    effective_policy = _resolve_policy(policy, company_context)
    state = _flow_state(flow)

    if flow and is_plain_greeting(message):
        return _resume_scheduling_result(state)

    proposal = _proposal_from_state(state) if flow else None
    prefix = ""
    action_override: str | None = None
    skip_message_parsing = False
    replaced_proposal: dict[str, Any] | None = None

    if proposal:
        interpretation = control.interpret_control_message(
            message, kind="create", service_names=_catalog_service_names(company_context)
        )
        if interpretation.intent == control.CONFIRM:
            cid = require_company_id(company_id, context="scheduling confirmation")
            request_summary = str(state.get("request_summary") or "Atendimento solicitado via bot")

            def _create_booking(params: dict[str, Any]) -> tuple[Any, dict[str, Any], int | None]:
                service = params.get("service_name")
                booking = create_calendar_booking(
                    company_id=cid,
                    contact_id=contact_id,
                    title=_summary_for_booking(
                        contact_id=contact_id,
                        contact_name=contact_name,
                        service_name=service,
                        request_summary=request_summary,
                    ),
                    starts_at=str(params["starts_at"]),
                    ends_at=str(params["ends_at"]),
                    timezone_name=str(params["timezone"]),
                    description=_description_for_booking(
                        contact_id=contact_id,
                        contact_name=contact_name,
                        service_name=service,
                        request_summary=request_summary,
                    ),
                    scheduling_profile_key=params.get("scheduling_profile_key"),
                    customer_profile=params.get("customer_profile"),
                    service_name=service,
                    request_context=request_summary,
                )
                return booking, _operation_result(booking, params.get("timezone")), int(booking["id"])

            execution = _accept_and_execute(
                company_id=cid,
                contact_id=contact_id,
                proposal=proposal,
                message_ref=message_ref,
                timezone_obj=timezone_obj,
                effect=_create_booking,
            )
            if execution.outcome == "completed":
                booking = execution.effect
                state["booking_id"] = booking["id"]
                _close_active_flow_if_needed(flow, status="completed", state=state)
                human_time = _human_time(booking["starts_at"], timezone_obj, booking.get("timezone"))
                return WorkflowResult(
                    handled=True,
                    classification="agendamento",
                    reply=(
                        f"Agendamento confirmado para {human_time}. "
                        "Já deixei tudo registrado com o resumo do atendimento para a equipe seguir com contexto."
                    ),
                    action="schedule_confirmed",
                    workflow_type="scheduling",
                    metadata={"booking_id": booking["id"], "operation_id": proposal["operation_id"]},
                )
            if execution.outcome == "replay":
                return _finish_replay(flow, state, execution, timezone_obj)
            if execution.outcome == "policy":
                return _policy_block_result(
                    kind="create",
                    label=str(proposal.get("label") or ""),
                    booking_id=None,
                    outcome=execution.policy_outcome,
                    reason=execution.policy_reason,
                    policy=effective_policy,
                    flow=flow,
                    state=state,
                    workflow_type="scheduling",
                )
            state.pop("proposal", None)
            state["selected_slot"] = None
            if execution.outcome == "conflict" and execution.operation:
                _exclude_slot(state, execution.operation["params"]["starts_at"])
                state["requested_time"] = None
                prefix = "Esse horário acabou de ser ocupado, então não consegui confirmar. "
                action_override = "schedule_slot_unavailable"
            else:
                prefix = _stale_prefix(execution.detail, "create")
        elif interpretation.intent == control.REJECT:
            _discard_proposal(company_id=company_id, contact_id=contact_id, proposal=proposal, timezone_obj=timezone_obj)
            return _abandon_scheduling_result(
                flow=flow, state=state, contact_id=contact_id, company_id=company_id, timezone_obj=timezone_obj
            )
        elif interpretation.intent in {control.QUESTION, control.UNCERTAIN}:
            return _scheduling_confirmation_reminder(state, proposal, interpretation.intent)
        else:
            # Mudança: a proposta atual deixa de valer; a nova a substitui.
            replaced_proposal = proposal
            state.pop("proposal", None)
            current_slot = state.get("selected_slot")
            state["selected_slot"] = None
            skip_message_parsing = _apply_slot_change(
                state, interpretation.change, current_slot, timezone_obj, allow_service=True
            )
    elif flow and control.is_abandon_request(message):
        return _abandon_scheduling_result(
            flow=flow, state=state, contact_id=contact_id, company_id=company_id, timezone_obj=timezone_obj
        )

    def _drop_replaced() -> None:
        _discard_proposal(
            company_id=company_id, contact_id=contact_id, proposal=replaced_proposal, timezone_obj=timezone_obj
        )

    service_name = state.get("service_name") or _extract_service_name(company_context, message)
    request_summary = state.get("request_summary") or _extract_request_summary(message)
    requested_date = state.get("requested_date")
    requested_time = state.get("requested_time")
    requested_period = state.get("requested_period")
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

    if not skip_message_parsing:
        parsed_date = _parse_natural_date(message, timezone_obj)
        parsed_time, parsed_period = _parse_natural_time(message)
        if parsed_date:
            requested_date = parsed_date.isoformat()
        if parsed_time:
            requested_time = parsed_time
        if parsed_period:
            requested_period = parsed_period

    if not service_name:
        _drop_replaced()
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
                f"{prefix}Consigo verificar isso para você. "
                "Me diz qual serviço ou tipo de atendimento você quer agendar para eu te oferecer os horários certos."
            ),
            workflow_type="scheduling",
        )

    if not requested_date:
        _drop_replaced()
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
                f"{prefix}{_build_scheduling_reply_prefix(service_name)} "
                "Qual dia fica melhor para você? Se preferir, pode me dizer algo como amanhã, sexta ou uma data específica."
            ),
            workflow_type="scheduling",
        )

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=requested_date,
        end_date=requested_date,
        duration_minutes=duration_minutes,
        limit=AVAILABILITY_QUERY_LIMIT,
        scheduling_profile_key=str(scheduling_profile_key) if scheduling_profile_key else None,
        customer_profile=str(customer_profile) if customer_profile is not None else None,
        service_name=service_name,
        request_context=request_summary,
    )
    slots = _without_excluded(_filter_slots_by_period(list(availability["slots"]), requested_period), state)

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
            human_date = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            operation = _persist_proposal(
                flow=flow,
                contact_id=contact_id,
                company_id=company_id,
                flow_type="scheduling",
                state=state,
                kind="create",
                params=_create_params(
                    slot=matching_slot,
                    service_name=service_name,
                    scheduling_profile_key=scheduling_profile_key,
                    customer_profile=customer_profile,
                ),
                policy=effective_policy,
                timezone_obj=timezone_obj,
                label=human_date,
            )
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=(
                    f"{prefix}Tenho esse horário disponível para {human_date}. "
                    "Se estiver tudo certo, me responde com um sim que eu já confirmo para você."
                ),
                action=action_override or "schedule_confirmation_requested",
                workflow_type="scheduling",
                metadata={"operation_id": operation["id"]},
            )

    if not slots:
        _drop_replaced()
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
                f"{prefix}Para {requested_date} eu não encontrei um horário livre dentro das regras atuais. "
                "Se quiser, posso verificar outro dia ou outro período para te sugerir as melhores opções."
            ),
            action=action_override or "workflow",
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
    operation = _persist_proposal(
        flow=flow,
        contact_id=contact_id,
        company_id=company_id,
        flow_type="scheduling",
        state=state,
        kind="create",
        params=_create_params(
            slot=selected_slot,
            service_name=service_name,
            scheduling_profile_key=scheduling_profile_key,
            customer_profile=customer_profile,
        ),
        policy=effective_policy,
        timezone_obj=timezone_obj,
        label=_format_slots_naturally([selected_slot]),
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"{prefix}{_build_scheduling_reply_prefix(service_name)} "
            f"Encontrei estes horários: {_format_slots_naturally(slots[:3])}. "
            "Se algum deles funcionar para você, me diz qual prefere ou responde com sim para eu confirmar a primeira opção."
        ),
        action=action_override or "schedule_options_presented",
        workflow_type="scheduling",
        metadata={"operation_id": operation["id"]},
    )


# ── fluxo de cancelamento/remarcação ──────────────────────────────────


def _resume_booking_change_result(
    state: dict[str, Any], upcoming_bookings: list[dict[str, Any]], timezone_obj: ZoneInfo
) -> WorkflowResult:
    proposal = _proposal_from_state(state)
    target = next(
        (booking for booking in upcoming_bookings if proposal and int(booking["id"]) == int(proposal.get("booking_id") or 0)),
        None,
    )
    if proposal and target:
        label = _format_booking_label(target, timezone_obj)
        if proposal.get("kind") == "cancel":
            step = (
                f"O cancelamento de {label} está aguardando sua confirmação. "
                "Se quiser seguir, me responde com um sim; se preferir manter, é só dizer."
            )
        else:
            step = (
                f"A remarcação de {label} para {proposal.get('label')} está aguardando sua confirmação. "
                "Se quiser seguir, me responde com um sim; se preferir manter, é só dizer."
            )
    elif not state.get("booking_id") and len(upcoming_bookings) > 1:
        step = _build_booking_change_selection_reply(upcoming_bookings, timezone_obj)
    elif state.get("action") == "reschedule_booking":
        step = "Me diz qual novo dia ou período funciona melhor para você."
    else:
        step = "Me diz se você quer cancelar ou remarcar o seu agendamento."
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=f"Oi! Seguimos de onde paramos. {step}",
        action="booking_change_resumed",
        workflow_type="booking_change",
    )


def _booking_change_confirmation_reminder(
    *, kind: str, proposal: dict[str, Any], booking_label: str, intent: str
) -> WorkflowResult:
    if kind == "cancel":
        if intent == control.QUESTION:
            reply = (
                f"Ainda não cancelei nada. Essa confirmação vale só para {booking_label}; "
                "nenhum outro agendamento é alterado. Se quiser seguir com o cancelamento, me responde com um sim. "
                "Se preferir manter, é só dizer."
            )
        else:
            reply = (
                "Não consegui entender se posso cancelar, então ainda não cancelei nada. "
                f"Se quiser cancelar {booking_label}, me responde com um sim. Se preferir manter, é só dizer."
            )
        action = "booking_cancel_confirmation_requested"
    else:
        new_label = proposal.get("label")
        if intent == control.QUESTION:
            reply = (
                f"Ainda não remarquei nada. A proposta é mudar {booking_label} para {new_label}, "
                "sem alterar outros agendamentos. Se estiver certo, me responde com um sim; "
                "se preferir manter, é só dizer."
            )
        else:
            reply = (
                "Não consegui entender se posso remarcar, então ainda não remarquei nada. "
                f"Se quiser mudar {booking_label} para {new_label}, me responde com um sim. "
                "Se preferir manter, é só dizer."
            )
        action = "booking_reschedule_confirmation_requested"
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=reply,
        workflow_type="booking_change",
        action=action,
        metadata={"booking_id": proposal.get("booking_id"), "operation_id": proposal.get("operation_id")},
    )


def _select_booking_for_change(
    *,
    bookings: list[dict[str, Any]],
    message: str,
    change: control.ControlChange,
    current_booking_id: int | None,
    timezone_obj: ZoneInfo,
) -> dict[str, Any] | None:
    if change.choice is not None and 1 <= change.choice <= len(bookings):
        return bookings[change.choice - 1]
    if change.dates:
        selected = _select_booking_from_message(bookings=bookings, message=message, timezone_obj=timezone_obj)
        if selected:
            return selected
    if change.other_option and len(bookings) == 2:
        return next((booking for booking in bookings if int(booking["id"]) != current_booking_id), None)
    return None


def _continue_booking_change_flow(
    *,
    flow: dict[str, Any] | None,
    contact_id: str,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
    policy: EffectivePolicy | None = None,
    message_ref: str | None = None,
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    effective_policy = _resolve_policy(policy, company_context)
    state = _flow_state(flow)
    proposal = _proposal_from_state(state) if flow else None
    upcoming_bookings = _get_upcoming_bookings(
        contact_id=contact_id,
        company_id=company_id,
        timezone_obj=timezone_obj,
    )

    if not upcoming_bookings:
        _discard_proposal(company_id=company_id, contact_id=contact_id, proposal=proposal, timezone_obj=timezone_obj)
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Eu não encontrei um agendamento ativo seu para alterar agora. Se quiser, posso te ajudar a marcar um novo horário.",
            workflow_type="booking_change",
            action="booking_change_not_found",
        )

    if flow and is_plain_greeting(message):
        return _resume_booking_change_result(state, upcoming_bookings, timezone_obj)

    prefix = ""
    action_override: str | None = None
    skip_message_parsing = False
    replaced_proposal: dict[str, Any] | None = None

    if proposal and not any(int(booking["id"]) == int(proposal.get("booking_id") or 0) for booking in upcoming_bookings):
        # O alvo deixou de estar ativo por outra via: a proposta não vale mais.
        _discard_proposal(company_id=company_id, contact_id=contact_id, proposal=proposal, timezone_obj=timezone_obj)
        prefix = _stale_prefix("target_changed", str(proposal.get("kind") or "cancel"))
        state.pop("proposal", None)
        state.pop("booking_id", None)
        state["selected_slot"] = None
        proposal = None

    if proposal:
        kind = str(proposal.get("kind") or "cancel")
        target_id = int(proposal["booking_id"])
        target_booking = next(booking for booking in upcoming_bookings if int(booking["id"]) == target_id)
        booking_label = _format_booking_label(target_booking, timezone_obj)
        interpretation = control.interpret_control_message(message, kind=kind)

        if interpretation.intent == control.CONFIRM:
            cid = require_company_id(company_id, context="booking change confirmation")

            def _cancel_booking(params: dict[str, Any]) -> tuple[Any, dict[str, Any], int | None]:
                released = release_booking_slot(booking_id=target_id, company_id=cid)
                cancelled = released.get("booking") if isinstance(released, dict) else None
                if isinstance(cancelled, dict) and cancelled.get("id"):
                    summary = _operation_result(cancelled)
                else:
                    summary = {"booking_id": target_id, "status": "cancelled"}
                return released, summary, target_id

            def _reschedule_booking(params: dict[str, Any]) -> tuple[Any, dict[str, Any], int | None]:
                rescheduled = reschedule_calendar_booking(
                    booking_id=target_id,
                    starts_at=str(params["starts_at"]),
                    ends_at=str(params["ends_at"]),
                    timezone_name=str(params["timezone"]),
                    scheduling_profile_key=str(target_booking.get("scheduling_profile_key") or "").strip() or None,
                    customer_profile=(
                        str(target_booking.get("customer_profile"))
                        if target_booking.get("customer_profile") is not None
                        else None
                    ),
                    company_id=cid,
                )
                return rescheduled, _operation_result(rescheduled, params.get("timezone")), target_id

            execution = _accept_and_execute(
                company_id=cid,
                contact_id=contact_id,
                proposal=proposal,
                message_ref=message_ref,
                timezone_obj=timezone_obj,
                effect=_cancel_booking if kind == "cancel" else _reschedule_booking,
                load_target=lambda: repository.get_appointment_booking(target_id, company_id=cid),
                precheck=lambda target: _evaluate_change(effective_policy, kind, target, timezone_obj),
            )
            if execution.outcome == "completed":
                _close_active_flow_if_needed(flow, status="completed", state=state)
                effect = execution.effect if isinstance(execution.effect, dict) else {}
                if kind == "cancel":
                    return WorkflowResult(
                        handled=True,
                        classification="agendamento",
                        reply=f"Pronto. Cancelei {booking_label}. Se quiser, eu também posso te ajudar a encontrar outra data.",
                        workflow_type="booking_change",
                        action="booking_cancelled",
                        metadata={
                            "booking": effect.get("booking"),
                            "queue_recovery": effect.get("queue_recovery"),
                            "operation_id": proposal["operation_id"],
                        },
                    )
                new_start = _human_time(effect["starts_at"], timezone_obj, effect.get("timezone"))
                return WorkflowResult(
                    handled=True,
                    classification="agendamento",
                    reply=f"Perfeito. Remarquei seu atendimento para {new_start}.",
                    workflow_type="booking_change",
                    action="booking_rescheduled",
                    metadata={"booking_id": effect.get("id"), "operation_id": proposal["operation_id"]},
                )
            if execution.outcome == "replay":
                return _finish_replay(flow, state, execution, timezone_obj)
            if execution.outcome == "policy":
                return _policy_block_result(
                    kind=kind,
                    label=booking_label,
                    booking_id=target_id,
                    outcome=execution.policy_outcome,
                    reason=execution.policy_reason,
                    policy=effective_policy,
                    flow=flow,
                    state=state,
                    workflow_type="booking_change",
                )
            state.pop("proposal", None)
            state["selected_slot"] = None
            if execution.outcome == "conflict" and execution.operation:
                _exclude_slot(state, execution.operation["params"]["starts_at"])
                state["requested_time"] = None
                prefix = "Esse horário acabou de ser ocupado, então não consegui remarcar. "
                action_override = "booking_reschedule_slot_unavailable"
            else:
                prefix = _stale_prefix(execution.detail, kind)
                if execution.outcome == "not_executed":
                    upcoming_bookings = _get_upcoming_bookings(
                        contact_id=contact_id, company_id=company_id, timezone_obj=timezone_obj
                    )
                    if not upcoming_bookings:
                        _close_active_flow_if_needed(flow, status="cancelled", state=state)
                        return WorkflowResult(
                            handled=True,
                            classification="agendamento",
                            reply=f"{prefix}Não encontrei mais um agendamento ativo seu para alterar agora.",
                            workflow_type="booking_change",
                            action="booking_change_not_found",
                        )
        elif interpretation.intent == control.REJECT:
            _discard_proposal(company_id=company_id, contact_id=contact_id, proposal=proposal, timezone_obj=timezone_obj)
            state.pop("proposal", None)
            _close_active_flow_if_needed(flow, status="cancelled", state=state)
            if kind == "cancel":
                reply = f"Tudo bem, não cancelei nada. {_capitalize(booking_label)} continua mantido."
            else:
                reply = f"Tudo bem, não alterei nada. {_capitalize(booking_label)} continua como estava."
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=reply,
                workflow_type="booking_change",
                action="booking_change_kept",
                metadata=_booking_change_result_metadata(target_booking),
            )
        elif interpretation.intent in {control.QUESTION, control.UNCERTAIN}:
            return _booking_change_confirmation_reminder(
                kind=kind, proposal=proposal, booking_label=booking_label, intent=interpretation.intent
            )
        else:
            replaced_proposal = proposal
            state.pop("proposal", None)
            change = interpretation.change
            if kind == "cancel":
                new_target = (
                    _select_booking_for_change(
                        bookings=upcoming_bookings,
                        message=message,
                        change=change,
                        current_booking_id=target_id,
                        timezone_obj=timezone_obj,
                    )
                    if change.dates or change.choice is not None or change.other_option
                    else None
                )
                if new_target is None:
                    # Horário/serviço citado num cancelamento é ambíguo: cancelar ou remarcar?
                    _discard_proposal(
                        company_id=company_id, contact_id=contact_id, proposal=proposal, timezone_obj=timezone_obj
                    )
                    state.pop("action", None)
                    repository.upsert_conversation_flow(
                        contact_id=contact_id, company_id=company_id, flow_type="booking_change", state=state
                    )
                    return WorkflowResult(
                        handled=True,
                        classification="agendamento",
                        reply=(
                            f"Não cancelei nada. Você quer cancelar {booking_label} ou remarcar para outro horário? "
                            "Me diz se prefere cancelar ou remarcar."
                        ),
                        workflow_type="booking_change",
                        action="booking_change_clarification_requested",
                    )
                state["booking_id"] = int(new_target["id"])
            else:
                current_slot = state.get("selected_slot")
                state["selected_slot"] = None
                skip_message_parsing = _apply_slot_change(
                    state, change, current_slot, timezone_obj, allow_service=False
                )
    elif flow and control.is_abandon_request(
        message, kind="reschedule" if state.get("action") == "reschedule_booking" else "cancel"
    ):
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Tudo bem, não alterei nenhum agendamento. Se precisar, é só me chamar.",
            workflow_type="booking_change",
            action="booking_change_kept",
        )

    def _drop_replaced() -> None:
        _discard_proposal(
            company_id=company_id, contact_id=contact_id, proposal=replaced_proposal, timezone_obj=timezone_obj
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
        _drop_replaced()
        _close_active_flow_if_needed(flow, status="completed", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Perfeito. Vou manter seu agendamento como está. Se quiser alterar depois, me chama por aqui.",
            workflow_type="booking_change",
            action="booking_change_kept",
        )

    if not selected_booking:
        _drop_replaced()
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"{prefix}{_build_booking_change_selection_reply(upcoming_bookings, timezone_obj)}",
            workflow_type="booking_change",
            action="booking_change_selection_requested",
            metadata={"booking_candidates": [_booking_change_result_metadata(booking) for booking in upcoming_bookings]},
        )

    state["booking_id"] = int(selected_booking["id"])
    booking_label = _format_booking_label(selected_booking, timezone_obj)

    if action not in {"cancel_booking", "reschedule_booking"}:
        _drop_replaced()
        _close_active_flow_if_needed(flow, status="cancelled", state=state)
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply="Entendi que você quer alterar um agendamento, mas ainda não consegui definir se é cancelamento ou remarcação. Me diz se você quer cancelar ou remarcar.",
            workflow_type="booking_change",
            action="booking_change_clarification_requested",
        )

    kind = "cancel" if action == "cancel_booking" else "reschedule"
    decision = _evaluate_change(effective_policy, kind, selected_booking, timezone_obj)
    if not decision.allowed:
        # Fora da regra não há proposta: encaminha ou explica (A08).
        _drop_replaced()
        return _policy_block_result(
            kind=kind,
            label=booking_label,
            booking_id=int(selected_booking["id"]),
            outcome=decision.outcome,
            reason=decision.reason,
            policy=effective_policy,
            flow=flow,
            state=state,
            workflow_type="booking_change",
        )

    booking_timezone = selected_booking.get("timezone")
    if kind == "cancel":
        operation = _persist_proposal(
            flow=flow,
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
            kind="cancel",
            params={
                "starts_at": _utc_iso(selected_booking["starts_at"], booking_timezone),
                "ends_at": _utc_iso(selected_booking["ends_at"], booking_timezone),
                "timezone": str(booking_timezone or timezone_obj),
            },
            policy=effective_policy,
            timezone_obj=timezone_obj,
            label=booking_label,
            booking=selected_booking,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"{prefix}Posso cancelar {booking_label}. Se quiser seguir, me responde com um sim.",
            workflow_type="booking_change",
            action="booking_cancel_confirmation_requested",
            metadata={**_booking_change_result_metadata(selected_booking), "operation_id": operation["id"]},
        )

    if not skip_message_parsing:
        parsed_date = _parse_natural_date(message, timezone_obj)
        parsed_time, parsed_period = _parse_natural_time(message)
        if parsed_date:
            state["requested_date"] = parsed_date.isoformat()
        if parsed_time:
            state["requested_time"] = parsed_time
        if parsed_period:
            state["requested_period"] = parsed_period

    if not state.get("requested_date"):
        _drop_replaced()
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"{prefix}Claro. Para remarcar {booking_label}, me diz qual novo dia ou período funciona melhor para você.",
            workflow_type="booking_change",
            action="booking_reschedule_date_requested",
            metadata=_booking_change_result_metadata(selected_booking),
        )

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=str(state["requested_date"]),
        end_date=str(state["requested_date"]),
        duration_minutes=_get_booking_duration_minutes(selected_booking),
        limit=AVAILABILITY_QUERY_LIMIT,
        scheduling_profile_key=str(selected_booking.get("scheduling_profile_key") or "").strip() or None,
        customer_profile=str(selected_booking.get("customer_profile")) if selected_booking.get("customer_profile") is not None else None,
        request_context=str(selected_booking.get("description") or selected_booking.get("title") or ""),
        ignore_booking_id=int(selected_booking["id"]),
    )
    slots = _without_excluded(_filter_slots_by_period(list(availability["slots"]), state.get("requested_period")), state)

    def _reschedule_params(slot: dict[str, Any]) -> dict[str, Any]:
        slot_timezone = str(slot.get("timezone") or booking_timezone or timezone_obj)
        return {
            "starts_at": _utc_iso(slot["starts_at"], slot_timezone),
            "ends_at": _utc_iso(slot["ends_at"], slot_timezone),
            "timezone": slot_timezone,
            "from_starts_at": _utc_iso(selected_booking["starts_at"], booking_timezone),
        }

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
            human_date = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            operation = _persist_proposal(
                flow=flow,
                contact_id=contact_id,
                company_id=company_id,
                flow_type="booking_change",
                state=state,
                kind="reschedule",
                params=_reschedule_params(matching_slot),
                policy=effective_policy,
                timezone_obj=timezone_obj,
                label=human_date,
                booking=selected_booking,
            )
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=f"{prefix}Tenho esse novo horário disponível: {human_date}. Se quiser, me responde com um sim que eu já remarco para você.",
                workflow_type="booking_change",
                action=action_override or "booking_reschedule_confirmation_requested",
                metadata={"booking_id": selected_booking["id"], "operation_id": operation["id"]},
            )

    if not slots:
        _drop_replaced()
        repository.upsert_conversation_flow(
            contact_id=contact_id,
            company_id=company_id,
            flow_type="booking_change",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"{prefix}Para esse dia eu não encontrei horário livre dentro das regras atuais. Se quiser, me fala outro dia ou outro período e eu continuo buscando.",
            workflow_type="booking_change",
            action=action_override or "booking_reschedule_no_slots",
        )

    state["candidate_slots"] = slots[:3]
    state["selected_slot"] = slots[0]
    operation = _persist_proposal(
        flow=flow,
        contact_id=contact_id,
        company_id=company_id,
        flow_type="booking_change",
        state=state,
        kind="reschedule",
        params=_reschedule_params(slots[0]),
        policy=effective_policy,
        timezone_obj=timezone_obj,
        label=_format_slots_naturally([slots[0]]),
        booking=selected_booking,
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"{prefix}Para remarcar {booking_label}, encontrei estas opções: {_format_slots_naturally(slots[:3])}. "
            "Se quiser, responde com sim para eu confirmar a primeira opção ou me diz qual prefere."
        ),
        workflow_type="booking_change",
        action=action_override or "booking_reschedule_options_presented",
        metadata={"booking_id": selected_booking["id"], "operation_id": operation["id"]},
    )


# ── fluxo interno do operador ─────────────────────────────────────────


def _resume_operator_result(state: dict[str, Any]) -> WorkflowResult:
    proposal = _proposal_from_state(state)
    if proposal:
        step = f"O horário de {proposal.get('label')} está aguardando sua confirmação. Se estiver certo, me responde com um sim."
    elif not state.get("selected_contact_id"):
        step = "Me confirma qual é o cliente para eu seguir com o agendamento."
    elif not state.get("requested_date"):
        step = "Me diz o dia ou período em que você quer encaixar esse cliente."
    else:
        step = "Me diz qual dia ou horário prefere para eu continuar."
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=f"Oi! Seguimos com o agendamento. {step}",
        workflow_type="operator_booking",
        action="operator_booking_resumed",
    )


def _continue_operator_booking_flow(
    *,
    flow: dict[str, Any] | None,
    operator_contact_id: str,
    company_id: int | None,
    message: str,
    company_context: dict[str, Any],
    policy: EffectivePolicy | None = None,
    message_ref: str | None = None,
) -> WorkflowResult:
    timezone_obj = _get_timezone(company_context)
    effective_policy = _resolve_policy(policy, company_context)
    state = _flow_state(flow)
    prefix = ""
    action_override: str | None = None
    skip_message_parsing = False
    replaced_proposal: dict[str, Any] | None = None

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
        # A mensagem inicial já foi lida; não relê como resposta de controle.
        skip_message_parsing = True
    else:
        if is_plain_greeting(message):
            return _resume_operator_result(state)
        proposal = _proposal_from_state(state)
        if proposal:
            interpretation = control.interpret_control_message(
                message, kind="create", service_names=_catalog_service_names(company_context)
            )
            if interpretation.intent == control.CONFIRM:
                cid = require_company_id(company_id, context="operator booking confirmation")
                operator_summary = str(state.get("request_summary") or "")
                matched = state.get("matched_contacts") or []
                target_contact = next(
                    (item for item in matched if item.get("contact_id") == state.get("selected_contact_id")),
                    {"contact_id": state.get("selected_contact_id")},
                )
                target_name = target_contact.get("contact_name")

                def _create_for_customer(params: dict[str, Any]) -> tuple[Any, dict[str, Any], int | None]:
                    customer_id = str(params["target_contact_id"])
                    service = params.get("service_name")
                    booking = create_calendar_booking(
                        company_id=cid,
                        contact_id=customer_id,
                        title=_summary_for_booking(
                            contact_id=customer_id,
                            contact_name=str(target_name) if target_name else None,
                            service_name=service,
                            request_summary=operator_summary or "Agendamento solicitado pelo operador.",
                        ),
                        starts_at=str(params["starts_at"]),
                        ends_at=str(params["ends_at"]),
                        timezone_name=str(params["timezone"]),
                        description=_description_for_booking(
                            contact_id=customer_id,
                            contact_name=str(target_name) if target_name else None,
                            service_name=service,
                            request_summary=f"Agendamento criado pelo operador {operator_contact_id}.",
                        ),
                        scheduling_profile_key=params.get("scheduling_profile_key"),
                        customer_profile=params.get("customer_profile"),
                        service_name=service,
                        request_context=operator_summary,
                    )
                    return booking, _operation_result(booking, params.get("timezone")), int(booking["id"])

                execution = _accept_and_execute(
                    company_id=cid,
                    contact_id=operator_contact_id,
                    proposal=proposal,
                    message_ref=message_ref,
                    timezone_obj=timezone_obj,
                    effect=_create_for_customer,
                )
                if execution.outcome == "completed":
                    booking = execution.effect
                    _close_active_flow_if_needed(flow, status="completed", state=state)
                    starts_at = _human_time(booking["starts_at"], timezone_obj, booking.get("timezone"))
                    return WorkflowResult(
                        handled=True,
                        classification="agendamento",
                        reply=(
                            f"Perfeito. Agendei {target_name or target_contact.get('contact_id')} "
                            f"para {starts_at}."
                        ),
                        workflow_type="operator_booking",
                        action="operator_booking_created",
                        metadata={
                            "booking_id": booking["id"],
                            "contact_id": target_contact.get("contact_id"),
                            "operation_id": proposal["operation_id"],
                        },
                    )
                if execution.outcome == "replay":
                    return _finish_replay(flow, state, execution, timezone_obj)
                if execution.outcome == "policy":
                    return _policy_block_result(
                        kind="create",
                        label=str(proposal.get("label") or ""),
                        booking_id=None,
                        outcome=execution.policy_outcome,
                        reason=execution.policy_reason,
                        policy=effective_policy,
                        flow=flow,
                        state=state,
                        workflow_type="operator_booking",
                    )
                state.pop("proposal", None)
                state["selected_slot"] = None
                if execution.outcome == "conflict" and execution.operation:
                    _exclude_slot(state, execution.operation["params"]["starts_at"])
                    state["requested_time"] = None
                    prefix = "Esse horário acabou de ser ocupado, então não consegui agendar. "
                    action_override = "operator_booking_slot_unavailable"
                else:
                    prefix = _stale_prefix(execution.detail, "create")
                skip_message_parsing = True
            elif interpretation.intent == control.REJECT:
                _discard_proposal(
                    company_id=company_id, contact_id=operator_contact_id, proposal=proposal, timezone_obj=timezone_obj
                )
                state.pop("proposal", None)
                _close_active_flow_if_needed(flow, status="cancelled", state=state)
                return WorkflowResult(
                    handled=True,
                    classification="agendamento",
                    reply=f"Tudo bem. Não agendei nada para {proposal.get('label')}.",
                    workflow_type="operator_booking",
                    action="operator_booking_abandoned",
                )
            elif interpretation.intent in {control.QUESTION, control.UNCERTAIN}:
                return WorkflowResult(
                    handled=True,
                    classification="agendamento",
                    reply=(
                        f"Ainda não agendei nada. A confirmação vale só para o horário de {proposal.get('label')}. "
                        "Se estiver certo, me responde com um sim; se preferir outro horário, é só dizer."
                    ),
                    workflow_type="operator_booking",
                    action="operator_booking_confirmation_requested",
                    metadata={"operation_id": proposal.get("operation_id")},
                )
            else:
                replaced_proposal = proposal
                state.pop("proposal", None)
                current_slot = state.get("selected_slot")
                state["selected_slot"] = None
                skip_message_parsing = _apply_slot_change(
                    state, interpretation.change, current_slot, timezone_obj, allow_service=True
                )
        elif control.is_abandon_request(message):
            _close_active_flow_if_needed(flow, status="cancelled", state=state)
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply="Tudo bem. Não vou seguir com esse agendamento agora.",
                workflow_type="operator_booking",
                action="operator_booking_abandoned",
            )

    def _drop_replaced() -> None:
        _discard_proposal(
            company_id=company_id, contact_id=operator_contact_id, proposal=replaced_proposal, timezone_obj=timezone_obj
        )

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
            if flow:
                # A mensagem que escolheu o contato não é resposta sobre data/horário.
                skip_message_parsing = True

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

    if not skip_message_parsing:
        parsed_date = _parse_natural_date(message, timezone_obj)
        parsed_time, parsed_period = _parse_natural_time(message)
        if parsed_date:
            state["requested_date"] = parsed_date.isoformat()
        if parsed_time:
            state["requested_time"] = parsed_time
        if parsed_period:
            state["requested_period"] = parsed_period

    if not state.get("requested_date"):
        _drop_replaced()
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

    service_name = state.get("service_name")
    customer_profile = state.get("customer_profile")
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        service_name=str(service_name) if service_name else None,
        customer_profile=str(customer_profile) if customer_profile else None,
        request_context=str(state.get("request_summary") or ""),
    )
    duration_minutes = _extract_slot_duration_minutes(company_context, scheduling_profile)
    scheduling_profile_key = scheduling_profile.get("key") if scheduling_profile else None
    contact_label = selected_contact.get("contact_name") or selected_contact.get("contact_id")

    availability = query_calendar_availability(
        company_id=company_id,
        start_date=str(state["requested_date"]),
        end_date=str(state["requested_date"]),
        duration_minutes=duration_minutes,
        limit=AVAILABILITY_QUERY_LIMIT,
        scheduling_profile_key=scheduling_profile_key,
        customer_profile=str(customer_profile) if customer_profile else None,
        service_name=str(service_name) if service_name else None,
        request_context=str(state.get("request_summary") or ""),
    )
    slots = _without_excluded(_filter_slots_by_period(list(availability["slots"]), state.get("requested_period")), state)

    def _operator_params(slot: dict[str, Any]) -> dict[str, Any]:
        return _create_params(
            slot=slot,
            service_name=service_name,
            scheduling_profile_key=scheduling_profile_key,
            customer_profile=customer_profile,
            target_contact_id=str(selected_contact["contact_id"]),
        )

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
            human_time = datetime.fromisoformat(str(matching_slot["starts_at"])).astimezone(timezone_obj).strftime("%d/%m às %H:%M")
            operation = _persist_proposal(
                flow=flow,
                contact_id=operator_contact_id,
                company_id=company_id,
                flow_type="operator_booking",
                state=state,
                kind="create",
                params=_operator_params(matching_slot),
                policy=effective_policy,
                timezone_obj=timezone_obj,
                label=human_time,
                source="operator",
            )
            return WorkflowResult(
                handled=True,
                classification="agendamento",
                reply=(
                    f"{prefix}Contato confirmado: {contact_label} | "
                    f"{selected_contact.get('contact_id')}. "
                    f"Tenho esse horário disponível: {human_time}. Se estiver certo, me responde com um sim."
                ),
                workflow_type="operator_booking",
                action=action_override or "operator_booking_confirmation_requested",
                metadata={"operation_id": operation["id"]},
            )

    if not slots:
        _drop_replaced()
        repository.upsert_conversation_flow(
            contact_id=operator_contact_id,
            company_id=company_id,
            flow_type="operator_booking",
            state=state,
        )
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=f"{prefix}Para esse dia eu não encontrei vaga disponível dentro das regras atuais. Se quiser, me passa outro dia ou período.",
            workflow_type="operator_booking",
            action=action_override or "operator_booking_no_slots",
        )

    state["candidate_slots"] = slots[:3]
    state["selected_slot"] = slots[0]
    operation = _persist_proposal(
        flow=flow,
        contact_id=operator_contact_id,
        company_id=company_id,
        flow_type="operator_booking",
        state=state,
        kind="create",
        params=_operator_params(slots[0]),
        policy=effective_policy,
        timezone_obj=timezone_obj,
        label=_format_slots_naturally([slots[0]]),
        source="operator",
    )
    return WorkflowResult(
        handled=True,
        classification="agendamento",
        reply=(
            f"{prefix}Contato confirmado: {contact_label} | {selected_contact.get('contact_id')}. "
            f"Encontrei estes horários: {_format_slots_naturally(slots[:3])}. "
            "Se quiser, responde com sim para eu confirmar a primeira opção ou me diz qual prefere."
        ),
        workflow_type="operator_booking",
        action=action_override or "operator_booking_options_presented",
        metadata={"operation_id": operation["id"]},
    )


def handle_conversation_workflow(
    *,
    contact_id: str,
    contact_name: str | None,
    message: str,
    company_id: int | None,
    company_context: dict[str, Any],
    policy: EffectivePolicy | None = None,
    message_ref: str | None = None,
    content_type: str = "text",
) -> WorkflowResult:
    """Conduz fluxos ativos de agenda.

    `policy`: política efetiva publicada (TTL de proposta, regras de
    cancelamento/remarcação). `message_ref`: identificador estável da mensagem
    de entrada (ex.: `whatsapp:<provider id>`), usado como evidência de aceite
    para que reentrega não confirme duas vezes.

    Nenhuma escrita de agenda acontece sem proposta persistida e aceite
    inequívoco vinculado a ela (CONTRATOS, "Interpretação e confirmação").
    """
    effective_policy = _resolve_policy(policy, company_context)
    active_flow = repository.get_active_conversation_flow(contact_id=contact_id, company_id=company_id)

    if (
        content_type in TAP_CONTENT_TYPES
        and _may_be_confirmation(message)
        and _has_pending_confirmation(company_id=company_id, contact_id=contact_id)
    ):
        # Toque em botão/lista (inclusive de template antigo) não carrega a
        # proposta a que responde; o piloto não envia botões de confirmação.
        # Ação de agenda ou aceite de encaixe exige "sim" digitado.
        return WorkflowResult(
            handled=True,
            classification="agendamento",
            reply=(
                "Para confirmar com segurança, responda por escrito com \"sim\" à proposta "
                "que te enviei. Se preferir, me diga o que você quer fazer."
            ),
            source="workflow",
            action="confirmation_requires_typed_reply",
            priority="high",
            auto_reply=True,
            workflow_type=str(active_flow.get("flow_type")) if active_flow else None,
        )
    active_flow_type = active_flow.get("flow_type") if active_flow else None
    normalized_message = _normalize_text(message)

    if message_ref and company_id is not None and _may_be_confirmation(message):
        # Reentrega de uma confirmação já consumida: responde o resultado conhecido.
        replayed = _operation_for_confirmation_ref(
            company_id=int(company_id), contact_id=contact_id, message_ref=message_ref
        )
        if replayed is not None:
            active_state = _flow_state(active_flow)
            active_proposal = _proposal_from_state(active_state)
            if (
                active_proposal
                and int(active_proposal["operation_id"]) == int(replayed["id"])
                and replayed.get("status") == "completed"
            ):
                _close_active_flow_if_needed(active_flow, status="completed", state=active_state)
            return _replay_result(replayed, _get_timezone(company_context))

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
            handoff_question_type=queue_result.get("handoff_question_type"),
            handoff_question=queue_result.get("handoff_question"),
        )

    if active_flow_type == "scheduling":
        return _continue_scheduling_flow(
            flow=active_flow,
            contact_id=contact_id,
            contact_name=contact_name,
            company_id=company_id,
            message=message,
            company_context=company_context,
            policy=effective_policy,
            message_ref=message_ref,
        )

    if active_flow_type == "booking_change":
        return _continue_booking_change_flow(
            flow=active_flow,
            contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
            policy=effective_policy,
            message_ref=message_ref,
        )

    if active_flow_type == "operator_booking":
        return _continue_operator_booking_flow(
            flow=active_flow,
            operator_contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
            policy=effective_policy,
            message_ref=message_ref,
        )

    if _is_operator_contact(company_context, contact_id) and _looks_like_operator_scheduling_message(normalized_message):
        operator_result = _continue_operator_booking_flow(
            flow=None,
            operator_contact_id=contact_id,
            company_id=company_id,
            message=message,
            company_context=company_context,
            policy=effective_policy,
            message_ref=message_ref,
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
            policy=effective_policy,
            message_ref=message_ref,
        )

    if _scheduling_enabled(company_context) and _mentions_scheduling(normalized_message):
        return _continue_scheduling_flow(
            flow=None,
            contact_id=contact_id,
            contact_name=contact_name,
            company_id=company_id,
            message=message,
            company_context=company_context,
            policy=effective_policy,
            message_ref=message_ref,
        )

    return WorkflowResult(handled=False)
