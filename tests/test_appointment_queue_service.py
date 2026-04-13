from app.services import appointment_queue_service as queue_service
from app.services import calendar_service
from app.core.config import settings


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

    def get_appointment_booking(self, booking_id: int):
        return self.bookings.get(booking_id)

    def update_appointment_booking_status(self, booking_id: int, status: str) -> None:
        self.bookings[booking_id]["status"] = status

    def cancel_customer_reminders_for_booking(self, appointment_booking_id: int) -> int:
        self.cancelled_reminders.append(appointment_booking_id)
        return 1

    def get_appointment_queue_entries(self, company_id=None, status=None):
        entries = list(self.queue_entries.values())
        if company_id is not None:
            entries = [entry for entry in entries if entry["company_id"] == company_id]
        if status is not None:
            entries = [entry for entry in entries if entry["status"] == status]
        return entries

    def get_appointment_queue_entry(self, entry_id: int):
        return self.queue_entries.get(entry_id)

    def update_appointment_queue_entry_status(self, entry_id: int, *, status: str, notes=None) -> None:
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

    def get_appointment_queue_offer(self, offer_id: int):
        return self.queue_offers.get(offer_id)

    def update_appointment_queue_offer(self, offer_id: int, **kwargs) -> None:
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


def test_release_booking_slot_creates_offer_for_highest_priority_candidate(monkeypatch) -> None:
    fake_repository = FakeQueueRepository()
    monkeypatch.setattr(queue_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "appointment_queue_auto_dispatch", True)
    monkeypatch.setattr(settings, "reminder_worker_channel", None)
    monkeypatch.setattr(settings, "outbound_default_channel", "whatsapp")
    monkeypatch.setattr(
        queue_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
        },
    )
    monkeypatch.setattr(
        calendar_service,
        "cancel_calendar_booking",
        lambda booking_id: {
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
        lambda outbound_message_id, channel=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
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
        lambda outbound_message_id, channel=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
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
        lambda outbound_message_id, channel=None: {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
        },
    )

    processed = queue_service.process_due_queue_offers()

    assert processed[0]["action"] == "follow_up_sent"
    assert fake_repository.queue_offers[201]["status"] == "pending"
    assert fake_repository.created_outbound_messages[0]["source"] == "appointment_queue_waiting_followup"


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
