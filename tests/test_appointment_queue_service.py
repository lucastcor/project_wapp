import pytest

from app.core.config import settings
from app.services import appointment_queue_service as queue_service
from app.services import calendar_service


class FakeQueueRepository:
    def __init__(self) -> None:
        self.bookings = {
            7: {
                "id": 7,
                "company_id": 1,
                "contact_id": "cliente-cancelado",
                "provider": "mock",
                "external_event_id": None,
                "scheduling_profile_key": "consulta",
                "customer_profile": "new_customer",
                "title": "Consulta",
                "description": "Horário liberado",
                "starts_at": "2030-04-10T13:00:00+00:00",
                "ends_at": "2030-04-10T14:00:00+00:00",
                "timezone": "America/Sao_Paulo",
                "status": "confirmed",
            },
            91: {
                "id": 91,
                "company_id": 1,
                "contact_id": "cliente-remanejamento",
                "provider": "mock",
                "external_event_id": None,
                "scheduling_profile_key": "consulta",
                "customer_profile": "returning_customer",
                "title": "Consulta",
                "description": "Booking antigo",
                "starts_at": "2030-04-15T13:00:00+00:00",
                "ends_at": "2030-04-15T14:00:00+00:00",
                "timezone": "America/Sao_Paulo",
                "status": "confirmed",
            },
        }
        self.queue_entries = {
            1: {
                "id": 1,
                "company_id": 1,
                "contact_id": "cliente-remanejamento",
                "desired_title": "Consulta VIP",
                "scheduling_profile_key": "consulta",
                "customer_profile": "returning_customer",
                "request_summary": "Cliente aceitaria antecipar",
                "timezone": "America/Sao_Paulo",
                "earliest_starts_at": None,
                "latest_starts_at": None,
                "preferred_periods": [],
                "priority_tier": "high",
                "priority_score": 50,
                "accepts_short_notice": 1,
                "original_booking_id": 91,
                "status": "active",
                "source": "reschedule_interest",
                "notes": None,
                "created_at": "2030-04-01T09:00:00+00:00",
            },
            2: {
                "id": 2,
                "company_id": 1,
                "contact_id": "cliente-lista",
                "desired_title": "Consulta comum",
                "scheduling_profile_key": "consulta",
                "customer_profile": "new_customer",
                "request_summary": "Entrou na fila",
                "timezone": "America/Sao_Paulo",
                "earliest_starts_at": None,
                "latest_starts_at": None,
                "preferred_periods": [],
                "priority_tier": "normal",
                "priority_score": 10,
                "accepts_short_notice": 1,
                "original_booking_id": None,
                "status": "active",
                "source": "waitlist",
                "notes": None,
                "created_at": "2030-04-01T10:00:00+00:00",
            },
        }
        self.queue_offers: dict[int, dict] = {}
        self.created_outbound_messages: list[dict] = []
        self.cancelled_reminders: list[int] = []
        self.booking_lookup_calls: list[dict] = []
        self.queue_entry_lookup_calls: list[dict] = []
        self.entry_status_updates: list[dict] = []
        self.offer_updates: list[dict] = []

    def get_appointment_booking(self, booking_id: int, *, company_id=None):
        self.booking_lookup_calls.append({"booking_id": booking_id, "company_id": company_id})
        booking = self.bookings.get(booking_id)
        if booking is None:
            return None
        if company_id is not None and booking.get("company_id") != company_id:
            return None
        return booking

    def get_appointment_bookings_between(self, starts_at, ends_at, company_id=None):
        from datetime import datetime

        window_start, window_end = datetime.fromisoformat(starts_at), datetime.fromisoformat(ends_at)
        return [
            booking
            for booking in self.bookings.values()
            if booking.get("status") in {"confirmed", "pending"}
            and (company_id is None or booking.get("company_id") == company_id)
            and datetime.fromisoformat(str(booking["starts_at"])) < window_end
            and datetime.fromisoformat(str(booking["ends_at"])) > window_start
        ]

    def update_appointment_booking_status(
        self,
        booking_id: int,
        status: str,
        *,
        company_id=None,
    ) -> None:
        self.bookings[booking_id]["status"] = status

    def cancel_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        *,
        company_id=None,
    ) -> int:
        self.cancelled_reminders.append(appointment_booking_id)
        return 1

    def get_appointment_queue_entries(self, company_id=None, status=None):
        entries = list(self.queue_entries.values())
        if company_id is not None:
            entries = [entry for entry in entries if entry["company_id"] == company_id]
        if status is not None:
            entries = [entry for entry in entries if entry["status"] == status]
        return entries

    def get_appointment_queue_entry(self, entry_id: int, *, company_id=None):
        self.queue_entry_lookup_calls.append({"entry_id": entry_id, "company_id": company_id})
        entry = self.queue_entries.get(entry_id)
        if entry is None:
            return None
        if company_id is not None and entry.get("company_id") != company_id:
            return None
        return entry

    def update_appointment_queue_entry_status(
        self,
        entry_id: int,
        *,
        status: str,
        notes=None,
        company_id=None,
    ) -> None:
        self.entry_status_updates.append(
            {
                "entry_id": entry_id,
                "status": status,
                "notes": notes,
                "company_id": company_id,
            }
        )
        self.queue_entries[entry_id]["status"] = status
        if notes is not None:
            self.queue_entries[entry_id]["notes"] = notes

    def get_appointment_queue_offers(self, company_id=None, status=None, released_booking_id=None, queue_entry_id=None):
        offers = list(self.queue_offers.values())
        if company_id is not None:
            offers = [offer for offer in offers if offer["company_id"] == company_id]
        if status is not None:
            offers = [offer for offer in offers if offer["status"] == status]
        if released_booking_id is not None:
            offers = [offer for offer in offers if offer["released_booking_id"] == released_booking_id]
        if queue_entry_id is not None:
            offers = [offer for offer in offers if offer["queue_entry_id"] == queue_entry_id]
        return offers

    def create_outbound_message(self, **kwargs):
        self.created_outbound_messages.append(kwargs)
        return 100 + len(self.created_outbound_messages)

    def create_appointment_queue_offer(self, **kwargs):
        offer_id = 200 + len(self.queue_offers) + 1
        self.queue_offers[offer_id] = {"id": offer_id, **kwargs}
        return offer_id

    def create_appointment_queue_entry(self, **kwargs):
        entry_id = 1000 + len(self.queue_entries) + 1
        self.queue_entries[entry_id] = {"id": entry_id, **kwargs}
        return entry_id

    def get_appointment_queue_offer(self, offer_id: int, *, company_id=None):
        offer = self.queue_offers.get(offer_id)
        if offer is None:
            return None
        if company_id is not None and offer.get("company_id") != company_id:
            return None
        return offer

    def update_appointment_queue_offer(self, offer_id: int, **kwargs) -> None:
        self.offer_updates.append({"offer_id": offer_id, **kwargs})
        kwargs.pop("company_id", None)
        self.queue_offers[offer_id].update(kwargs)


class FakeQueueLLMProvider:
    def __init__(self, intent: str, reason: str = "interpreted by llm") -> None:
        self.intent = intent
        self.reason = reason

    def interpret_appointment_queue_reply(self, **kwargs):
        return {
            "intent": self.intent,
            "confidence": 0.94,
            "reason": self.reason,
        }


def test_create_queue_entry_uses_company_scope_for_original_booking_lookup(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)

    entry = queue_service.create_queue_entry(
        company_id=1,
        contact_id="cliente-novo",
        desired_title="Consulta derivada",
        original_booking_id=91,
    )

    assert entry["company_id"] == 1
    assert entry["original_booking_id"] == 91
    assert fake_repository.booking_lookup_calls == [{"booking_id": 91, "company_id": 1}]
    assert fake_repository.queue_entry_lookup_calls[-1] == {
        "entry_id": entry["id"],
        "company_id": 1,
    }


def test_release_booking_slot_creates_offer_for_highest_priority_candidate(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    monkeypatch.setattr(settings, "reminder_worker_channel", None)
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")
    monkeypatch.setattr(
        queue_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
            "company_id": company_id,
        },
    )
    monkeypatch.setattr(
        calendar_service,
        "cancel_calendar_booking",
        lambda booking_id, company_id=None: {
            "booking": {**fake_repository.bookings[booking_id], "status": "cancelled"},
            "cancelled_reminders": 1,
            "external_sync": {"status": "not_required"},
        },
    )

    result = queue_service.release_booking_slot(booking_id=7)

    assert result["booking"]["status"] == "cancelled"
    assert result["cancelled_reminders"] == 1
    assert result["external_sync"]["status"] == "not_required"
    assert result["queue_recovery"]["created_offer"]["queue_entry_id"] == 1
    assert fake_repository.created_outbound_messages[0]["contact_id"] == "cliente-remanejamento"
    assert fake_repository.created_outbound_messages[0]["company_id"] == 1
    assert fake_repository.queue_entries[1]["status"] == "contacted"


def test_respond_to_queue_offer_declined_moves_to_next_candidate(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "pending",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": None,
        "responded_at": None,
        "response_notes": None,
    }
    fake_repository.queue_entries[1]["status"] = "contacted"
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    monkeypatch.setattr(settings, "reminder_worker_channel", None)
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")
    monkeypatch.setattr(
        queue_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
            "company_id": company_id,
        },
    )

    result = queue_service.respond_to_queue_offer(
        offer_id=201,
        response_status="declined",
        response_notes="Não consegue nesse horário.",
    )

    assert result["offer"]["status"] == "declined"
    assert result["entry"]["status"] == "awaiting_confirmation"
    assert result["next_offer"]["queue_entry_id"] == 2
    assert fake_repository.created_outbound_messages[0]["contact_id"] == "cliente-remanejamento"
    assert fake_repository.created_outbound_messages[1]["contact_id"] == "cliente-lista"


def test_respond_to_queue_offer_accepted_creates_booking(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "pending",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": None,
        "responded_at": None,
        "response_notes": None,
    }
    fake_repository.queue_entries[1]["status"] = "contacted"
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    monkeypatch.setattr(
        queue_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
            "company_id": company_id,
        },
    )
    monkeypatch.setattr(
        calendar_service,
        "reschedule_calendar_booking",
        lambda **kwargs: {
            "id": kwargs["booking_id"],
            "company_id": 1,
            "contact_id": "cliente-remanejamento",
            "title": kwargs["title"],
            "starts_at": kwargs["starts_at"],
            "ends_at": kwargs["ends_at"],
            "timezone": kwargs["timezone_name"],
            "provider": "mock",
            "scheduled_reminders": [],
        },
    )
    monkeypatch.setattr(calendar_service, "create_calendar_booking", lambda **kwargs: {"id": 555, **kwargs})

    result = queue_service.respond_to_queue_offer(
        offer_id=201,
        response_status="accepted",
        response_notes="Cliente confirmou o remanejamento.",
    )

    assert result["offer"]["status"] == "accepted"
    assert result["offer"]["new_booking_id"] == 91
    assert result["entry"]["status"] == "converted"
    assert result["new_booking"]["id"] == 91
    assert result["rescheduled_booking_id"] == 91


def test_process_due_queue_offers_sends_followup_after_wait_window(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_entries[1]["status"] = "contacted"
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "waiting",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": "2000-01-01T00:00:00+00:00",
        "responded_at": None,
        "response_notes": None,
        "created_at": "2030-04-10T11:00:00+00:00",
    }
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    monkeypatch.setattr(settings, "appointment_queue_response_window_minutes", 20)
    monkeypatch.setattr(
        queue_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
            "company_id": company_id,
        },
    )

    processed = queue_service.process_due_queue_offers()

    assert processed[0]["action"] == "follow_up_sent"
    assert fake_repository.queue_offers[201]["status"] == "pending"
    assert fake_repository.created_outbound_messages[0]["source"] == "appointment_queue_waiting_followup"
    assert fake_repository.offer_updates[0]["company_id"] == 1


def test_process_queue_contact_message_keeps_customer_in_queue_without_losing_priority(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_entries[1]["status"] = "awaiting_confirmation"
    monkeypatch.setattr(queue_service, "repository", fake_repository)

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="sim, pode me manter na fila",
    )

    assert result is not None
    assert result["action"] == "queue_membership_confirmed"
    assert fake_repository.queue_entries[1]["status"] == "active"


def test_process_queue_contact_message_uses_llm_for_natural_wait_reply(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_entries[1]["status"] = "contacted"
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "pending",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": None,
        "responded_at": None,
        "response_notes": None,
        "created_at": "2030-04-10T11:00:00+00:00",
    }
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "appointment_queue_wait_followup_minutes", 15)
    monkeypatch.setattr(
        queue_service,
        "OpenAIProvider",
        lambda: FakeQueueLLMProvider("wait", "Cliente pediu um momento para confirmar."),
    )

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="ahh vou confirmar aqui um momento",
    )

    assert result is not None
    assert result["action"] == "queue_offer_waiting"
    assert fake_repository.queue_offers[201]["status"] == "waiting"
    assert fake_repository.queue_offers[201]["follow_up_after"] is not None


def test_process_queue_contact_message_llm_keeps_customer_in_queue_after_declining_slot(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_entries[1]["status"] = "contacted"
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "pending",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": None,
        "responded_at": None,
        "response_notes": None,
        "created_at": "2030-04-10T11:00:00+00:00",
    }
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(
        queue_service,
        "OpenAIProvider",
        lambda: FakeQueueLLMProvider(
            "decline_offer_keep_queue",
            "Cliente recusou esta vaga, mas quer continuar aguardando outras.",
        ),
    )

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="nesse horário não consigo, mas pode me manter na fila se abrir outra vaga",
    )

    assert result is not None
    assert result["action"] == "queue_offer_declined_keep_queue"
    assert fake_repository.queue_offers[201]["status"] == "declined"
    assert fake_repository.queue_entries[1]["status"] == "active"
    assert result["metadata"]["next_offer"]["queue_entry_id"] == 2
    assert fake_repository.created_outbound_messages[0]["contact_id"] == "cliente-lista"


def test_process_queue_contact_message_uses_llm_for_queue_membership_confirmation(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.queue_entries[1]["status"] = "awaiting_confirmation"
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(
        queue_service,
        "OpenAIProvider",
        lambda: FakeQueueLLMProvider(
            "continue_queue",
            "Cliente quer continuar recebendo oportunidades futuras.",
        ),
    )

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="pode me deixar nas próximas oportunidades sim",
    )

    assert result is not None
    assert result["action"] == "queue_membership_confirmed"
    assert fake_repository.queue_entries[1]["status"] == "active"


# ── P0 da revisão de P02: resposta à oferta de encaixe ─────────────────
#
# A heurística antiga procurava substring e testava aceite antes de recusa:
# com uma oferta aberta, "não quero" (contém "quero") e "assim" (contém "sim")
# reservavam a vaga. Negação e pergunta nunca podem reservar.


def _with_pending_offer(fake_repository: "FakeQueueRepository") -> None:
    fake_repository.queue_entries[1]["status"] = "contacted"
    fake_repository.queue_offers[201] = {
        "id": 201,
        "company_id": 1,
        "queue_entry_id": 1,
        "released_booking_id": 7,
        "outbound_message_id": 101,
        "new_booking_id": None,
        "offered_starts_at": "2030-04-10T13:00:00+00:00",
        "offered_ends_at": "2030-04-10T14:00:00+00:00",
        "timezone": "America/Sao_Paulo",
        "status": "pending",
        "response_deadline_at": "2030-04-10T12:30:00+00:00",
        "follow_up_after": None,
        "responded_at": None,
        "response_notes": None,
        "created_at": "2030-04-10T11:00:00+00:00",
    }


@pytest.mark.parametrize(
    "message",
    ["não quero", "assim está bom?", "isso é pra quando?", "quero ver outra data?", "não quero essa vaga"],
)
def test_negative_or_question_never_accepts_a_queue_offer(monkeypatch, message: str) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message=message,
    )

    assert result is not None
    assert result["action"] != "queue_offer_accepted"
    assert fake_repository.queue_offers[201]["status"] != "accepted"


def test_llm_suggestion_cannot_accept_an_offer_without_unambiguous_confirmation(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(queue_service, "OpenAIProvider", lambda: FakeQueueLLMProvider("accept_offer"))

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="assim está bom?",
    )

    assert result is not None
    assert result["action"] == "queue_offer_response_requested"
    assert fake_repository.queue_offers[201]["status"] == "pending"


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("sim", "accept_offer"),
        ("não quero", "decline_offer"),
        ("assim está bom?", "unclear"),
        ("vou verificar e já te falo", "wait"),
        ("não consigo, pode me manter na fila", "decline_offer_keep_queue"),
        ("não, pode tirar da fila", "decline_offer_leave_queue"),
    ],
)
def test_offer_reply_heuristic_puts_negation_and_waiting_before_acceptance(message: str, intent: str) -> None:
    assert queue_service._fallback_interpret_slot_offer_reply(message)["intent"] == intent


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("sim, pode me manter na fila", "continue_queue"),
        ("pode tirar da fila", "leave_queue"),
        ("assim tá bom", "unclear"),
        ("não quero sair da fila", "unclear"),
    ],
)
def test_queue_membership_heuristic_uses_whole_words(message: str, intent: str) -> None:
    assert queue_service._fallback_interpret_queue_membership_reply(message)["intent"] == intent


# ── P04: encaixe desacoplado, capacidade de fila e vaga já ocupada ─────


@pytest.fixture(autouse=True)
def _queue_capability_enabled(monkeypatch):
    # Os repositórios falsos usam empresas que não existem no banco; a
    # capacidade é ligada aqui e desligada explicitamente no teste que a cobre.
    monkeypatch.setattr(queue_service, "_queue_enabled", lambda company_id: True)


def _cancel_stub(fake_repository):
    return lambda booking_id, company_id=None, **kwargs: {
        "booking": {**fake_repository.bookings[booking_id], "status": "cancelled"},
        "cancelled_reminders": 1,
        "external_sync": {"status": "not_required"},
    }


def test_cancellation_succeeds_when_offering_the_slot_fails(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "cancel_calendar_booking", _cancel_stub(fake_repository))

    def broken_offer(**kwargs):
        raise RuntimeError("synthetic offer failure")

    monkeypatch.setattr(queue_service, "offer_released_booking_slot", broken_offer)

    result = queue_service.release_booking_slot(booking_id=7)

    assert result["booking"]["status"] == "cancelled"
    assert result["queue_recovery"]["status"] == "deferred"
    assert result["queue_recovery"]["created_offer"] is None


def test_disabled_queue_capability_prevents_offers(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(queue_service, "_queue_enabled", lambda company_id: False)

    result = queue_service.offer_released_booking_slot(released_booking_id=7)

    assert result["status"] == "queue_disabled"
    assert result["created_offer"] is None
    assert fake_repository.created_outbound_messages == []
    assert fake_repository.queue_offers == {}


def test_offer_is_persisted_before_the_message_is_dispatched(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    offers_seen_at_dispatch: list[int] = []

    def dispatch(outbound_message_id, channel=None, company_id=None):
        offers_seen_at_dispatch.append(len(fake_repository.queue_offers))
        return {"outbound_message_id": outbound_message_id}

    monkeypatch.setattr(queue_service, "dispatch_outbound_message", dispatch)

    result = queue_service.offer_released_booking_slot(released_booking_id=7)

    assert result["created_offer"] is not None
    assert offers_seen_at_dispatch == [1]
    assert fake_repository.created_outbound_messages[0]["source"] == "appointment_queue_offer"


def test_accepting_an_offer_whose_slot_was_taken_explains_and_keeps_priority(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "mock")

    def taken(**kwargs):
        raise calendar_service.BookingConflictError("O horário solicitado conflita com um agendamento já existente.")

    monkeypatch.setattr(calendar_service, "reschedule_calendar_booking", taken)
    monkeypatch.setattr(calendar_service, "create_calendar_booking", taken)

    result = queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento",
        company_id=1,
        message="sim",
    )

    assert result is not None
    assert result["action"] == "queue_offer_slot_taken"
    assert fake_repository.queue_offers[201]["status"] == "expired"
    assert fake_repository.queue_entries[1]["status"] == "active"


def test_recovery_sweep_offers_cancelled_slots_left_without_offer(company_factory, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from app.storage.repository import RuntimeRepository

    real_repository = RuntimeRepository()
    monkeypatch.setattr(queue_service, "repository", real_repository)
    company_id = company_factory("queue-recovery", business_type="services")
    starts_at = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    booking_id = real_repository.create_appointment_booking(
        provider="mock",
        title="Consulta",
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(hours=1)).isoformat(),
        timezone="America/Sao_Paulo",
        company_id=company_id,
        contact_id="5511900000777",
    )
    real_repository.update_appointment_booking_status(booking_id, "cancelled", company_id=company_id)
    queue_service.create_queue_entry(
        company_id=company_id,
        contact_id="5511900000778",
        desired_title="Consulta",
        timezone="America/Sao_Paulo",
    )
    offered: list[int] = []
    monkeypatch.setattr(
        queue_service,
        "offer_released_booking_slot",
        lambda released_booking_id, company_id=None: offered.append(released_booking_id) or {"created_offer": {"id": 1}},
    )

    recovered = queue_service.recover_deferred_queue_offers()

    assert booking_id in offered
    assert {"released_booking_id": booking_id, "action": "deferred_offer_created"} in recovered


# ── revisão P04: oferta não promete vaga ocupada nem repetida ─────────


def test_offer_is_not_sent_when_the_released_slot_was_booked_again(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.bookings[7]["status"] = "cancelled"
    fake_repository.bookings[8] = {**fake_repository.bookings[7], "id": 8, "contact_id": "outro", "status": "confirmed"}
    monkeypatch.setattr(queue_service, "repository", fake_repository)

    result = queue_service.offer_released_booking_slot(released_booking_id=7)

    assert result["status"] == "slot_unavailable"
    assert fake_repository.queue_offers == {}
    assert fake_repository.created_outbound_messages == []


def test_the_same_interval_is_not_offered_to_two_contacts(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    fake_repository.bookings[7]["status"] = "cancelled"
    fake_repository.bookings[8] = {**fake_repository.bookings[7], "id": 8, "status": "cancelled"}
    monkeypatch.setattr(queue_service, "repository", fake_repository)

    result = queue_service.offer_released_booking_slot(released_booking_id=8)

    assert result["status"] == "slot_already_offered"
    assert list(fake_repository.queue_offers) == [201]


def test_human_owned_contacts_do_not_receive_automatic_offers(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    fake_repository.bookings[7]["status"] = "cancelled"
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(queue_service, "_contact_is_human_owned", lambda company_id, contact_id: True)

    result = queue_service.offer_released_booking_slot(released_booking_id=7)

    assert result["created_offer"] is None
    assert fake_repository.created_outbound_messages == []


@pytest.mark.parametrize(
    ("error", "action", "entry_status", "note"),
    [
        (calendar_service.ExternalCalendarUncertainError("sem resposta"), "queue_offer_booking_uncertain", "contacted",
         "booking_uncertain"),
        (calendar_service.CalendarProviderError("antecedência mínima"), "queue_offer_booking_refused", "active",
         "booking_refused"),
    ],
)
def test_accepted_offer_that_cannot_be_booked_answers_the_customer(
    monkeypatch, error, action, entry_status, note
) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "mock")

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(calendar_service, "reschedule_calendar_booking", fail)
    monkeypatch.setattr(calendar_service, "create_calendar_booking", fail)

    result = queue_service.process_queue_contact_message(contact_id="cliente-remanejamento", company_id=1, message="sim")

    assert result is not None
    assert result["action"] == action
    assert (fake_repository.queue_offers[201]["status"], fake_repository.queue_offers[201]["response_notes"]) == (
        "expired", note
    )
    assert fake_repository.queue_entries[1]["status"] == entry_status
    assert bool(result.get("handoff_question_type")) is (action == "queue_offer_booking_uncertain")


def test_reply_after_the_offer_deadline_does_not_accept(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    _with_pending_offer(fake_repository)
    fake_repository.queue_offers[201]["response_deadline_at"] = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "classification_provider", "mock")

    def must_not_book(**kwargs):
        raise AssertionError("oferta vencida não reserva")

    monkeypatch.setattr(calendar_service, "reschedule_calendar_booking", must_not_book)
    monkeypatch.setattr(calendar_service, "create_calendar_booking", must_not_book)

    assert queue_service.process_queue_contact_message(
        contact_id="cliente-remanejamento", company_id=1, message="sim"
    ) is None
