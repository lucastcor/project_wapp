import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.llm.providers.openai_provider import OpenAIProvider
from app.services.outbound_dispatch_service import (
    OutboundDispatchError,
    dispatch_outbound_message,
)
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()
VALID_PRIORITY_TIERS = {"vip", "high", "normal", "low"}
VALID_QUEUE_ENTRY_STATUSES = {
    "active",
    "contacted",
    "awaiting_confirmation",
    "converted",
    "paused",
    "removed",
}
VALID_QUEUE_OFFER_STATUSES = {"pending", "accepted", "declined", "waiting", "expired"}
VALID_PREFERRED_PERIODS = {"morning", "afternoon", "evening"}
SHORT_NOTICE_THRESHOLD_HOURS = 24
DEFAULT_RESPONSE_WINDOW_MINUTES = 30
DEFAULT_WAIT_WINDOW_MINUTES = 30
QUEUE_WAITING_TERMS = {
    "aguarde",
    "aguarda",
    "aguarda um pouco",
    "espera",
    "espera um pouco",
    "vou verificar",
    "vou ver",
    "vou confirmar",
    "vou checar",
    "vou consultar",
    "deixa eu ver",
    "deixa-me ver",
    "um momento",
    "só um instante",
    "so um instante",
}
QUEUE_CONFIRMATION_TERMS = {
    "sim",
    "confirmo",
    "pode confirmar",
    "pode agendar",
    "pode reservar",
    "pode marcar",
    "aceito",
    "quero",
}
QUEUE_DECLINE_TERMS = {
    "nao",
    "não",
    "nao consigo",
    "não consigo",
    "nao posso",
    "não posso",
}
QUEUE_KEEP_WAITING_TERMS = {
    "me mantém na fila",
    "me mantem na fila",
    "manter na fila",
    "continuar na fila",
    "seguir na fila",
    "continuar aguardando",
    "seguir aguardando",
    "outra vaga",
    "outra oportunidade",
    "outro horário",
    "outro horario",
    "próximas vagas",
    "proximas vagas",
    "pode me avisar",
}
QUEUE_LEAVE_TERMS = {
    "tirar da fila",
    "tira da fila",
    "sair da fila",
    "me remover",
    "pode remover",
    "não quero continuar",
    "nao quero continuar",
    "não precisa avisar",
    "nao precisa avisar",
    "não precisa manter",
    "nao precisa manter",
}
VALID_QUEUE_REPLY_INTENTS = {
    "accept_offer",
    "wait",
    "decline_offer",
    "decline_offer_keep_queue",
    "decline_offer_leave_queue",
    "continue_queue",
    "leave_queue",
    "unclear",
}
logger = get_logger(__name__)


class AppointmentQueueError(Exception):
    pass


def _parse_iso_datetime(value: str, timezone_name: str | None = None) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo(timezone_name or "UTC"))
    return parsed


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _normalize_preferred_periods(periods: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for item in periods or []:
        candidate = str(item).strip().lower()
        if candidate in VALID_PREFERRED_PERIODS and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _slot_period(slot_start: datetime) -> str:
    hour = slot_start.hour
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def _priority_weight(entry: dict[str, Any]) -> tuple[int, int, int, str, int]:
    tier_weight = {
        "vip": 4,
        "high": 3,
        "normal": 2,
        "low": 1,
    }.get(str(entry.get("priority_tier") or "normal").lower(), 2)
    reschedule_weight = 1 if entry.get("original_booking_id") else 0
    return (
        int(entry.get("priority_score") or 0),
        tier_weight,
        reschedule_weight,
    )


def _build_offer_message(
    *,
    desired_title: str,
    starts_at: datetime,
) -> str:
    human_time = starts_at.strftime("%d/%m às %H:%M")
    return (
        f"Abriu uma vaga para {desired_title} em {human_time}. "
        "Se esse horário fizer sentido para você, eu já posso reservar. "
        "Se não encaixar, me fala que eu sigo acompanhando outras oportunidades. "
        "Se precisar de alguns minutos para confirmar, tudo bem também."
    )


def _build_keep_waiting_message(*, desired_title: str) -> str:
    return (
        f"Sem problema com essa vaga de {desired_title}. "
        "Se você quiser, eu te mantenho na fila de prioridade para as próximas oportunidades que abrirem. "
        "Quer que eu siga te avisando quando aparecer algo compatível?"
    )


def _build_waiting_ack_message(*, wait_minutes: int) -> str:
    return (
        f"Perfeito. Pode verificar com calma. Eu seguro essa vaga por cerca de {wait_minutes} minuto(s) e depois volto a falar com você."
    )


def _build_waiting_followup_message(*, desired_title: str, starts_at: datetime) -> str:
    human_time = starts_at.strftime("%d/%m às %H:%M")
    return (
        f"Passando para confirmar se você conseguiu verificar. A vaga de {desired_title} em {human_time} ainda está reservada para você. "
        "Se quiser seguir com ela, eu confirmo por aqui. Se não encaixar, também me fala."
    )


def _matches_queue_entry(
    *,
    entry: dict[str, Any],
    slot_start: datetime,
    released_booking: dict[str, Any] | None = None,
) -> bool:
    timezone_name = str(entry.get("timezone") or slot_start.tzinfo or "UTC")
    localized_slot = slot_start.astimezone(ZoneInfo(timezone_name))

    entry_profile_key = str(entry.get("scheduling_profile_key") or "").strip().lower()
    released_profile_key = str((released_booking or {}).get("scheduling_profile_key") or "").strip().lower()
    if entry_profile_key and released_profile_key and entry_profile_key != released_profile_key:
        return False

    earliest_starts_at = entry.get("earliest_starts_at")
    if earliest_starts_at:
        earliest = _parse_iso_datetime(str(earliest_starts_at), timezone_name)
        if localized_slot < earliest.astimezone(ZoneInfo(timezone_name)):
            return False

    latest_starts_at = entry.get("latest_starts_at")
    if latest_starts_at:
        latest = _parse_iso_datetime(str(latest_starts_at), timezone_name)
        if localized_slot > latest.astimezone(ZoneInfo(timezone_name)):
            return False

    preferred_periods = _normalize_preferred_periods(entry.get("preferred_periods"))
    if preferred_periods and _slot_period(localized_slot) not in preferred_periods:
        return False

    hours_until_slot = (slot_start.astimezone(UTC) - datetime.now(UTC)).total_seconds() / 3600
    if hours_until_slot < SHORT_NOTICE_THRESHOLD_HOURS and not bool(entry.get("accepts_short_notice")):
        return False

    return True


def _is_queue_waiting_message(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in QUEUE_WAITING_TERMS)


def _is_queue_confirmation_message(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in QUEUE_CONFIRMATION_TERMS)


def _is_queue_decline_message(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in QUEUE_DECLINE_TERMS)


def _wants_to_keep_waiting(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in QUEUE_KEEP_WAITING_TERMS)


def _wants_to_leave_queue(message: str) -> bool:
    normalized = _normalize_text(message)
    return any(term in normalized for term in QUEUE_LEAVE_TERMS)


def _interpret_queue_reply_with_llm(
    *,
    stage: str,
    message: str,
    desired_title: str | None = None,
    offered_starts_at: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any] | None:
    if not settings.openai_api_key:
        return None

    try:
        provider = OpenAIProvider()
        result = provider.interpret_appointment_queue_reply(
            stage=stage,
            customer_message=message,
            desired_title=desired_title,
            offered_starts_at=offered_starts_at,
            timezone_name=timezone_name,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "appointment_queue.reply_interpretation.fallback",
            stage=stage,
            error_type=type(exc).__name__,
        )
        return None

    normalized_intent = str(result.get("intent") or "").strip().lower()
    if normalized_intent not in VALID_QUEUE_REPLY_INTENTS:
        log_event(
            logger,
            logging.WARNING,
            "appointment_queue.reply_interpretation.invalid_intent",
            stage=stage,
            intent=normalized_intent,
        )
        return None

    return {
        "intent": normalized_intent,
        "confidence": float(result.get("confidence") or 0),
        "reason": str(result.get("reason") or "").strip(),
        "source": "llm",
    }


def _fallback_interpret_slot_offer_reply(message: str) -> dict[str, Any]:
    if _is_queue_waiting_message(message):
        intent = "wait"
        reason = "Heurística detectou pedido de tempo para verificar a vaga."
    elif _is_queue_confirmation_message(message):
        intent = "accept_offer"
        reason = "Heurística detectou confirmação da vaga."
    elif _is_queue_decline_message(message):
        if _wants_to_leave_queue(message):
            intent = "decline_offer_leave_queue"
            reason = "Heurística detectou recusa da vaga com saída da fila."
        elif _wants_to_keep_waiting(message):
            intent = "decline_offer_keep_queue"
            reason = "Heurística detectou recusa da vaga com permanência na fila."
        else:
            intent = "decline_offer"
            reason = "Heurística detectou recusa da vaga atual."
    else:
        intent = "unclear"
        reason = "Sem sinal suficiente para decidir a intenção da resposta."

    return {
        "intent": intent,
        "confidence": 0.55 if intent != "unclear" else 0.25,
        "reason": reason,
        "source": "heuristic",
    }


def _fallback_interpret_queue_membership_reply(message: str) -> dict[str, Any]:
    if _is_queue_confirmation_message(message) or _wants_to_keep_waiting(message):
        intent = "continue_queue"
        reason = "Heurística detectou permanência na fila."
    elif _is_queue_decline_message(message) or _wants_to_leave_queue(message):
        intent = "leave_queue"
        reason = "Heurística detectou saída da fila."
    else:
        intent = "unclear"
        reason = "Sem sinal suficiente para decidir permanência na fila."

    return {
        "intent": intent,
        "confidence": 0.55 if intent != "unclear" else 0.25,
        "reason": reason,
        "source": "heuristic",
    }


def _interpret_slot_offer_reply(
    *,
    message: str,
    entry: dict[str, Any],
    offer: dict[str, Any],
) -> dict[str, Any]:
    llm_result = _interpret_queue_reply_with_llm(
        stage="slot_offer",
        message=message,
        desired_title=str(entry.get("desired_title") or ""),
        offered_starts_at=str(offer.get("offered_starts_at") or ""),
        timezone_name=str(offer.get("timezone") or entry.get("timezone") or "UTC"),
    )
    if llm_result:
        return llm_result
    return _fallback_interpret_slot_offer_reply(message)


def _interpret_queue_membership_reply(
    *,
    message: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    llm_result = _interpret_queue_reply_with_llm(
        stage="queue_membership",
        message=message,
        desired_title=str(entry.get("desired_title") or ""),
        timezone_name=str(entry.get("timezone") or "UTC"),
    )
    if llm_result:
        return llm_result
    return _fallback_interpret_queue_membership_reply(message)


def _dispatch_queue_outbound_message(
    outbound_message_id: int,
    *,
    company_id: int | None = None,
) -> dict[str, Any] | None:
    if not settings.appointment_queue_auto_dispatch:
        return None

    try:
        return dispatch_outbound_message(
            outbound_message_id=outbound_message_id,
            channel=settings.reminder_worker_channel or settings.outbound_default_channel,
            company_id=company_id,
        )
    except OutboundDispatchError as exc:
        log_event(
            logger,
            logging.WARNING,
            "appointment_queue.outbound.dispatch_failed",
            outbound_message_id=outbound_message_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def _create_and_dispatch_outbound(
    *,
    company_id: int | None,
    contact_id: str,
    message: str,
    source: str,
) -> tuple[int, dict[str, Any] | None]:
    outbound_message_id = repository.create_outbound_message(
        contact_id=contact_id,
        company_id=company_id,
        message=message,
        source=source,
        status="pending",
    )
    dispatch_result = _dispatch_queue_outbound_message(
        outbound_message_id,
        company_id=company_id,
    )
    return outbound_message_id, dispatch_result


def _find_offer_for_contact(
    *,
    contact_id: str,
    company_id: int | None = None,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    matching_entries = [
        entry
        for entry in repository.get_appointment_queue_entries(company_id=company_id, status=None)
        if str(entry.get("contact_id") or "") == contact_id
    ]
    if not matching_entries:
        return None

    allowed_statuses = statuses or {"pending", "waiting"}
    entry_ids = {int(entry["id"]) for entry in matching_entries}
    offers = [
        offer
        for offer in repository.get_appointment_queue_offers(company_id=company_id, status=None)
        if int(offer["queue_entry_id"]) in entry_ids
        and str(offer.get("status") or "") in allowed_statuses
    ]
    offers.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)), reverse=True)
    return offers[0] if offers else None


def _find_entry_for_contact(
    *,
    contact_id: str,
    company_id: int | None = None,
    status: str,
) -> dict[str, Any] | None:
    entries = [
        entry
        for entry in repository.get_appointment_queue_entries(company_id=company_id, status=status)
        if str(entry.get("contact_id") or "") == contact_id
    ]
    entries.sort(key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)), reverse=True)
    return entries[0] if entries else None


def create_queue_entry(
    *,
    company_id: int | None,
    contact_id: str,
    desired_title: str,
    scheduling_profile_key: str | None = None,
    customer_profile: str | None = None,
    request_summary: str | None = None,
    timezone: str | None = None,
    earliest_starts_at: str | None = None,
    latest_starts_at: str | None = None,
    preferred_periods: list[str] | None = None,
    priority_tier: str = "normal",
    priority_score: int = 0,
    accepts_short_notice: bool = True,
    original_booking_id: int | None = None,
    source: str = "manual",
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_contact_id = contact_id.strip()
    normalized_title = desired_title.strip()
    if not normalized_contact_id:
        raise AppointmentQueueError("contact_id must be a non-empty string.")
    if not normalized_title:
        raise AppointmentQueueError("desired_title must be a non-empty string.")

    normalized_tier = priority_tier.strip().lower() or "normal"
    if normalized_tier not in VALID_PRIORITY_TIERS:
        raise AppointmentQueueError(
            f"priority_tier must be one of: {', '.join(sorted(VALID_PRIORITY_TIERS))}."
        )

    resolved_company_id = company_id
    resolved_timezone = (timezone or "").strip() or None
    if original_booking_id is not None:
        original_booking = repository.get_appointment_booking(
            original_booking_id,
            company_id=company_id,
        )
        if not original_booking:
            raise AppointmentQueueError("original_booking_id must reference an existing booking.")
        if resolved_company_id is None:
            resolved_company_id = original_booking.get("company_id")
        if not resolved_timezone:
            resolved_timezone = str(original_booking.get("timezone") or "UTC")
        if not scheduling_profile_key:
            scheduling_profile_key = str(original_booking.get("scheduling_profile_key") or "").strip() or None
        if customer_profile is None:
            customer_profile = original_booking.get("customer_profile")

    normalized_timezone = resolved_timezone or "UTC"
    normalized_periods = _normalize_preferred_periods(preferred_periods)
    if preferred_periods and not normalized_periods:
        raise AppointmentQueueError(
            f"preferred_periods must use only: {', '.join(sorted(VALID_PREFERRED_PERIODS))}."
        )

    if earliest_starts_at and latest_starts_at:
        earliest = _parse_iso_datetime(earliest_starts_at, normalized_timezone)
        latest = _parse_iso_datetime(latest_starts_at, normalized_timezone)
        if latest <= earliest:
            raise AppointmentQueueError("latest_starts_at must be greater than earliest_starts_at.")

    entry_id = repository.create_appointment_queue_entry(
        company_id=resolved_company_id,
        contact_id=normalized_contact_id,
        desired_title=normalized_title,
        scheduling_profile_key=(scheduling_profile_key or "").strip() or None,
        customer_profile=str(customer_profile) if customer_profile is not None else None,
        request_summary=(request_summary or "").strip() or None,
        timezone=normalized_timezone,
        earliest_starts_at=earliest_starts_at,
        latest_starts_at=latest_starts_at,
        preferred_periods=normalized_periods,
        priority_tier=normalized_tier,
        priority_score=int(priority_score),
        accepts_short_notice=accepts_short_notice,
        original_booking_id=original_booking_id,
        source=(source or "manual").strip() or "manual",
        notes=(notes or "").strip() or None,
    )
    entry = repository.get_appointment_queue_entry(entry_id, company_id=resolved_company_id)
    if not entry:
        raise AppointmentQueueError("Queue entry not found after creation.")
    return entry


def list_queue_entries(
    *,
    company_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status and status not in VALID_QUEUE_ENTRY_STATUSES:
        raise AppointmentQueueError(
            f"status must be one of: {', '.join(sorted(VALID_QUEUE_ENTRY_STATUSES))}."
        )
    return repository.get_appointment_queue_entries(company_id=company_id, status=status)


def list_queue_offers(
    *,
    company_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status and status not in VALID_QUEUE_OFFER_STATUSES:
        raise AppointmentQueueError(
            f"status must be one of: {', '.join(sorted(VALID_QUEUE_OFFER_STATUSES))}."
        )
    return repository.get_appointment_queue_offers(company_id=company_id, status=status)


def offer_released_booking_slot(
    *,
    released_booking_id: int,
    response_window_minutes: int = DEFAULT_RESPONSE_WINDOW_MINUTES,
    company_id: int | None = None,
) -> dict[str, Any]:
    booking = repository.get_appointment_booking(released_booking_id, company_id=company_id)
    if not booking:
        raise AppointmentQueueError("Released booking not found.")

    company_id = booking.get("company_id")
    timezone_name = str(booking.get("timezone") or "UTC")
    slot_start = _parse_iso_datetime(str(booking["starts_at"]), timezone_name)

    existing_offers = repository.get_appointment_queue_offers(
        company_id=company_id,
        released_booking_id=released_booking_id,
    )
    already_contacted_entry_ids = {
        int(offer["queue_entry_id"])
        for offer in existing_offers
    }

    candidates = [
        entry
        for entry in repository.get_appointment_queue_entries(company_id=company_id, status="active")
        if int(entry["id"]) not in already_contacted_entry_ids
        and entry.get("contact_id") != booking.get("contact_id")
        and _matches_queue_entry(entry=entry, slot_start=slot_start, released_booking=booking)
    ]
    candidates.sort(key=_priority_weight, reverse=True)

    if not candidates:
        return {
            "released_booking_id": released_booking_id,
            "candidate_count": 0,
            "created_offer": None,
        }

    selected_entry = candidates[0]
    response_deadline_at = _to_utc_iso(datetime.now(UTC) + timedelta(minutes=max(5, response_window_minutes)))
    outbound_message_id, dispatch_result = _create_and_dispatch_outbound(
        company_id=company_id,
        contact_id=str(selected_entry["contact_id"]),
        message=_build_offer_message(
            desired_title=str(selected_entry["desired_title"]),
            starts_at=slot_start.astimezone(ZoneInfo(timezone_name)),
        ),
        source="appointment_queue_offer",
    )
    offer_id = repository.create_appointment_queue_offer(
        company_id=company_id,
        queue_entry_id=int(selected_entry["id"]),
        released_booking_id=released_booking_id,
        outbound_message_id=outbound_message_id,
        new_booking_id=None,
        offered_starts_at=str(booking["starts_at"]),
        offered_ends_at=str(booking["ends_at"]),
        timezone=timezone_name,
        status="pending",
        response_deadline_at=response_deadline_at,
    )
    repository.update_appointment_queue_entry_status(
        int(selected_entry["id"]),
        status="contacted",
        company_id=company_id,
    )
    created_offer = repository.get_appointment_queue_offer(offer_id, company_id=company_id)
    if not created_offer:
        raise AppointmentQueueError("Queue offer not found after creation.")

    return {
        "released_booking_id": released_booking_id,
        "candidate_count": len(candidates),
        "created_offer": created_offer,
        "dispatch_result": dispatch_result,
    }


def release_booking_slot(
    *,
    booking_id: int,
    company_id: int | None = None,
) -> dict[str, Any]:
    from app.services.calendar_service import CalendarProviderError, cancel_calendar_booking

    try:
        cancellation_result = cancel_calendar_booking(booking_id=booking_id, company_id=company_id)
    except CalendarProviderError as exc:
        raise AppointmentQueueError(str(exc)) from exc

    next_offer = offer_released_booking_slot(released_booking_id=booking_id, company_id=company_id)

    return {
        "booking": cancellation_result["booking"],
        "cancelled_reminders": cancellation_result["cancelled_reminders"],
        "external_sync": cancellation_result["external_sync"],
        "queue_recovery": next_offer,
    }


def respond_to_queue_offer(
    *,
    offer_id: int,
    response_status: str,
    wait_minutes: int | None = None,
    response_notes: str | None = None,
    queue_membership_after_decline: str = "ask",
    send_customer_followup: bool = True,
    company_id: int | None = None,
) -> dict[str, Any]:
    normalized_response = response_status.strip().lower()
    if normalized_response not in {"accepted", "declined", "waiting"}:
        raise AppointmentQueueError("response_status must be accepted, declined or waiting.")

    normalized_queue_membership = queue_membership_after_decline.strip().lower()
    if normalized_queue_membership not in {"ask", "keep", "remove"}:
        raise AppointmentQueueError("queue_membership_after_decline must be ask, keep or remove.")

    offer = repository.get_appointment_queue_offer(offer_id, company_id=company_id)
    if not offer:
        raise AppointmentQueueError("Queue offer not found.")
    if str(offer.get("status") or "") not in {"pending", "waiting"}:
        raise AppointmentQueueError("Only pending or waiting offers can be updated.")

    offer_company_id = offer.get("company_id")
    entry = repository.get_appointment_queue_entry(
        int(offer["queue_entry_id"]),
        company_id=offer_company_id,
    )
    if not entry:
        raise AppointmentQueueError("Queue entry not found for offer.")

    responded_at = _to_utc_iso(datetime.now(UTC))

    if normalized_response == "waiting":
        normalized_wait_minutes = max(5, int(wait_minutes or DEFAULT_WAIT_WINDOW_MINUTES))
        follow_up_after = _to_utc_iso(datetime.now(UTC) + timedelta(minutes=normalized_wait_minutes))
        repository.update_appointment_queue_offer(
            offer_id,
            status="waiting",
            response_deadline_at=offer.get("response_deadline_at"),
            follow_up_after=follow_up_after,
            responded_at=responded_at,
            response_notes=(response_notes or "").strip() or None,
            company_id=offer_company_id,
        )
        dispatch_result = None
        if send_customer_followup:
            _, dispatch_result = _create_and_dispatch_outbound(
                company_id=entry.get("company_id"),
                contact_id=str(entry["contact_id"]),
                message=_build_waiting_ack_message(wait_minutes=normalized_wait_minutes),
                source="appointment_queue_waiting_ack",
            )
        return {
            "offer": repository.get_appointment_queue_offer(offer_id, company_id=offer_company_id),
            "entry": repository.get_appointment_queue_entry(int(entry["id"]), company_id=offer_company_id),
            "next_offer": None,
            "dispatch_result": dispatch_result,
        }

    if normalized_response == "declined":
        repository.update_appointment_queue_offer(
            offer_id,
            status="declined",
            response_deadline_at=offer.get("response_deadline_at"),
            follow_up_after=None,
            responded_at=responded_at,
            response_notes=(response_notes or "").strip() or None,
            company_id=offer_company_id,
        )
        if normalized_queue_membership == "keep":
            repository.update_appointment_queue_entry_status(
                int(entry["id"]),
                status="active",
                notes="Customer declined the current slot but chose to remain in the appointment queue.",
                company_id=offer_company_id,
            )
        elif normalized_queue_membership == "remove":
            repository.update_appointment_queue_entry_status(
                int(entry["id"]),
                status="removed",
                notes="Customer declined the current slot and chose to leave the appointment queue.",
                company_id=offer_company_id,
            )
        else:
            repository.update_appointment_queue_entry_status(
                int(entry["id"]),
                status="awaiting_confirmation",
                notes="Awaiting keep-waiting confirmation after declined slot offer.",
                company_id=offer_company_id,
            )
        continuation_dispatch_result = None
        if send_customer_followup and normalized_queue_membership == "ask":
            _, continuation_dispatch_result = _create_and_dispatch_outbound(
                company_id=entry.get("company_id"),
                contact_id=str(entry["contact_id"]),
                message=_build_keep_waiting_message(
                    desired_title=str(entry["desired_title"]),
                ),
                source="appointment_queue_keep_waiting",
            )
        next_offer = offer_released_booking_slot(
            released_booking_id=int(offer["released_booking_id"]),
            company_id=offer_company_id,
        )
        return {
            "offer": repository.get_appointment_queue_offer(offer_id, company_id=offer_company_id),
            "entry": repository.get_appointment_queue_entry(int(entry["id"]), company_id=offer_company_id),
            "next_offer": next_offer.get("created_offer"),
            "dispatch_result": continuation_dispatch_result,
        }

    from app.services.calendar_service import create_calendar_booking, reschedule_calendar_booking

    request_summary = (entry.get("request_summary") or "").strip() or None
    original_booking_id = entry.get("original_booking_id")
    rescheduled_booking_id: int | None = None
    if original_booking_id:
        rescheduled_booking_id = int(original_booking_id)
        booking = reschedule_calendar_booking(
            booking_id=rescheduled_booking_id,
            starts_at=str(offer["offered_starts_at"]),
            ends_at=str(offer["offered_ends_at"]),
            timezone_name=str(offer["timezone"]),
            title=str(entry["desired_title"]),
            description=request_summary,
            scheduling_profile_key=str(entry.get("scheduling_profile_key") or "").strip() or None,
            customer_profile=str(entry.get("customer_profile")) if entry.get("customer_profile") is not None else None,
            request_context=request_summary,
            company_id=offer_company_id,
        )
    else:
        booking = create_calendar_booking(
            company_id=entry.get("company_id"),
            contact_id=str(entry["contact_id"]),
            title=str(entry["desired_title"]),
            starts_at=str(offer["offered_starts_at"]),
            ends_at=str(offer["offered_ends_at"]),
            timezone_name=str(offer["timezone"]),
            description=request_summary,
            scheduling_profile_key=str(entry.get("scheduling_profile_key") or "").strip() or None,
            customer_profile=str(entry.get("customer_profile")) if entry.get("customer_profile") is not None else None,
            request_context=request_summary,
        )

    repository.update_appointment_queue_offer(
        offer_id,
        status="accepted",
        new_booking_id=int(booking["id"]),
        response_deadline_at=offer.get("response_deadline_at"),
        follow_up_after=None,
        responded_at=responded_at,
        response_notes=(response_notes or "").strip() or None,
        company_id=offer_company_id,
    )
    repository.update_appointment_queue_entry_status(
        int(entry["id"]),
        status="converted",
        company_id=offer_company_id,
    )
    return {
        "offer": repository.get_appointment_queue_offer(offer_id, company_id=offer_company_id),
        "entry": repository.get_appointment_queue_entry(int(entry["id"]), company_id=offer_company_id),
        "new_booking": booking,
        "rescheduled_booking_id": rescheduled_booking_id,
        "next_offer": None,
    }


def process_due_queue_offers() -> list[dict[str, Any]]:
    reference_time = datetime.now(UTC)
    processed: list[dict[str, Any]] = []

    pending_offers = repository.get_appointment_queue_offers(status=None)
    due_waiting_offers = [
        offer
        for offer in pending_offers
        if str(offer.get("status") or "") == "waiting"
        and offer.get("follow_up_after")
        and _parse_iso_datetime(str(offer["follow_up_after"])) <= reference_time
    ]
    due_expired_offers = [
        offer
        for offer in pending_offers
        if str(offer.get("status") or "") == "pending"
        and offer.get("response_deadline_at")
        and _parse_iso_datetime(str(offer["response_deadline_at"])) <= reference_time
    ]

    for offer in due_waiting_offers:
        offer_company_id = offer.get("company_id")
        entry = repository.get_appointment_queue_entry(
            int(offer["queue_entry_id"]),
            company_id=offer_company_id,
        )
        if not entry:
            continue
        follow_up_message = _build_waiting_followup_message(
            desired_title=str(entry["desired_title"]),
            starts_at=_parse_iso_datetime(str(offer["offered_starts_at"]), str(offer.get("timezone") or "UTC")).astimezone(
                ZoneInfo(str(offer.get("timezone") or "UTC"))
            ),
        )
        outbound_message_id, dispatch_result = _create_and_dispatch_outbound(
            company_id=entry.get("company_id"),
            contact_id=str(entry["contact_id"]),
            message=follow_up_message,
            source="appointment_queue_waiting_followup",
        )
        repository.update_appointment_queue_offer(
            int(offer["id"]),
            status="pending",
            outbound_message_id=outbound_message_id,
            response_deadline_at=_to_utc_iso(
                reference_time + timedelta(minutes=max(5, settings.appointment_queue_response_window_minutes))
            ),
            follow_up_after=None,
            responded_at=None,
            response_notes="Automatic follow-up sent after waiting window.",
            company_id=offer_company_id,
        )
        processed.append(
            {
                "offer_id": offer["id"],
                "action": "follow_up_sent",
                "outbound_message_id": outbound_message_id,
                "dispatch_result": dispatch_result,
            }
        )

    for offer in due_expired_offers:
        offer_company_id = offer.get("company_id")
        repository.update_appointment_queue_offer(
            int(offer["id"]),
            status="expired",
            response_deadline_at=str(offer.get("response_deadline_at") or ""),
            follow_up_after=None,
            responded_at=_to_utc_iso(reference_time),
            response_notes="Offer expired without customer confirmation.",
            company_id=offer_company_id,
        )
        entry = repository.get_appointment_queue_entry(
            int(offer["queue_entry_id"]),
            company_id=offer_company_id,
        )
        if entry and str(entry.get("status") or "") == "contacted":
            repository.update_appointment_queue_entry_status(
                int(entry["id"]),
                status="active",
                notes="Reactivated after queue offer expiration.",
                company_id=offer_company_id,
            )
        next_offer = offer_released_booking_slot(
            released_booking_id=int(offer["released_booking_id"]),
            company_id=offer_company_id,
        )
        processed.append(
            {
                "offer_id": offer["id"],
                "action": "expired",
                "next_offer": next_offer.get("created_offer"),
            }
        )

    return processed


def process_queue_contact_message(
    *,
    contact_id: str,
    company_id: int | None,
    message: str,
) -> dict[str, Any] | None:
    pending_offer = _find_offer_for_contact(contact_id=contact_id, company_id=company_id)
    if pending_offer:
        pending_offer_company_id = pending_offer.get("company_id")
        entry = repository.get_appointment_queue_entry(
            int(pending_offer["queue_entry_id"]),
            company_id=pending_offer_company_id,
        )
        if not entry:
            raise AppointmentQueueError("Queue entry not found for pending offer.")

        interpretation = _interpret_slot_offer_reply(
            message=message,
            entry=entry,
            offer=pending_offer,
        )
        intent = str(interpretation["intent"])

        if intent == "accept_offer":
            result = respond_to_queue_offer(
                offer_id=int(pending_offer["id"]),
                response_status="accepted",
                response_notes=str(interpretation.get("reason") or "").strip() or None,
                send_customer_followup=False,
                company_id=pending_offer_company_id,
            )
            new_booking = result.get("new_booking") or {}
            booking_time = ""
            if isinstance(new_booking, dict) and new_booking.get("starts_at"):
                starts_at = _parse_iso_datetime(
                    str(new_booking["starts_at"]),
                    str(new_booking.get("timezone") or pending_offer.get("timezone") or "UTC"),
                ).astimezone(ZoneInfo(str(new_booking.get("timezone") or pending_offer.get("timezone") or "UTC")))
                booking_time = starts_at.strftime("%d/%m às %H:%M")
            return {
                "reply": (
                    f"Perfeito. Reservei esse horário para você em {booking_time}."
                    if booking_time
                    else "Perfeito. Reservei essa vaga para você."
                ),
                "action": "queue_offer_accepted",
                "workflow_type": "appointment_queue",
                "metadata": result,
            }

        if intent == "wait":
            wait_minutes = settings.appointment_queue_wait_followup_minutes
            result = respond_to_queue_offer(
                offer_id=int(pending_offer["id"]),
                response_status="waiting",
                wait_minutes=wait_minutes,
                response_notes=str(interpretation.get("reason") or "").strip()
                or "Customer asked for time to check availability.",
                send_customer_followup=False,
                company_id=pending_offer_company_id,
            )
            return {
                "reply": _build_waiting_ack_message(wait_minutes=wait_minutes),
                "action": "queue_offer_waiting",
                "workflow_type": "appointment_queue",
                "metadata": result,
            }

        if intent == "decline_offer":
            result = respond_to_queue_offer(
                offer_id=int(pending_offer["id"]),
                response_status="declined",
                response_notes=str(interpretation.get("reason") or "").strip()
                or "Customer declined the released slot.",
                send_customer_followup=False,
                company_id=pending_offer_company_id,
            )
            return {
                "reply": _build_keep_waiting_message(
                    desired_title=str(result["entry"]["desired_title"]),
                ),
                "action": "queue_offer_declined",
                "workflow_type": "appointment_queue",
                "metadata": result,
            }

        if intent == "decline_offer_keep_queue":
            result = respond_to_queue_offer(
                offer_id=int(pending_offer["id"]),
                response_status="declined",
                queue_membership_after_decline="keep",
                response_notes=str(interpretation.get("reason") or "").strip()
                or "Customer declined the released slot but chose to remain in the queue.",
                send_customer_followup=False,
                company_id=pending_offer_company_id,
            )
            return {
                "reply": (
                    "Sem problema com essa vaga. Vou te manter na fila de prioridade e te aviso "
                    "assim que surgir uma nova oportunidade compatível."
                ),
                "action": "queue_offer_declined_keep_queue",
                "workflow_type": "appointment_queue",
                "metadata": result,
            }

        if intent == "decline_offer_leave_queue":
            result = respond_to_queue_offer(
                offer_id=int(pending_offer["id"]),
                response_status="declined",
                queue_membership_after_decline="remove",
                response_notes=str(interpretation.get("reason") or "").strip()
                or "Customer declined the released slot and chose to leave the queue.",
                send_customer_followup=False,
                company_id=pending_offer_company_id,
            )
            return {
                "reply": (
                    "Tudo certo. Não vou seguir com essa vaga e também retirei você da fila por enquanto. "
                    "Se quiser voltar depois, é só me chamar."
                ),
                "action": "queue_offer_declined_removed",
                "workflow_type": "appointment_queue",
                "metadata": result,
            }

        return {
            "reply": (
                "Sem problema. Me diz só se você quer que eu reserve essa vaga, "
                "se prefere continuar aguardando outra oportunidade ou se precisa de alguns minutos para confirmar."
            ),
            "action": "queue_offer_response_requested",
            "workflow_type": "appointment_queue",
            "metadata": {"offer_id": pending_offer["id"], "interpretation": interpretation},
        }

    queue_confirmation_entry = _find_entry_for_contact(
        contact_id=contact_id,
        company_id=company_id,
        status="awaiting_confirmation",
    )
    if queue_confirmation_entry:
        interpretation = _interpret_queue_membership_reply(
            message=message,
            entry=queue_confirmation_entry,
        )
        intent = str(interpretation["intent"])

        if intent == "continue_queue":
            repository.update_appointment_queue_entry_status(
                int(queue_confirmation_entry["id"]),
                status="active",
                notes="Customer chose to remain in the appointment queue.",
                company_id=queue_confirmation_entry.get("company_id"),
            )
            return {
                "reply": "Perfeito. Vou te manter na fila de prioridade e te aviso assim que surgir uma vaga compatível.",
                "action": "queue_membership_confirmed",
                "workflow_type": "appointment_queue",
                "metadata": {"queue_entry_id": queue_confirmation_entry["id"]},
            }
        if intent == "leave_queue":
            repository.update_appointment_queue_entry_status(
                int(queue_confirmation_entry["id"]),
                status="removed",
                notes="Customer chose to leave the appointment queue.",
                company_id=queue_confirmation_entry.get("company_id"),
            )
            return {
                "reply": "Tudo certo. Tirei você da fila de prioridade. Se quiser voltar depois, é só me avisar.",
                "action": "queue_membership_removed",
                "workflow_type": "appointment_queue",
                "metadata": {"queue_entry_id": queue_confirmation_entry["id"]},
            }
        return {
            "reply": (
                "Tudo certo. Só me confirma se você quer continuar na fila de prioridade para próximas vagas "
                "ou se prefere sair dela por enquanto."
            ),
            "action": "queue_membership_response_requested",
            "workflow_type": "appointment_queue",
            "metadata": {
                "queue_entry_id": queue_confirmation_entry["id"],
                "interpretation": interpretation,
            },
        }

    return None
