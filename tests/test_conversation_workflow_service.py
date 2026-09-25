from app.services import conversation_workflow_service as workflow_service
from app.storage.repository import RuntimeRepository

# Fluxos e agenda seguem falsos nestes testes de unidade, mas desde P02 toda
# confirmação passa por proposta persistida em `booking_operations`, cuja FK
# exige empresa real e, para cancelamento/remarcação/conclusão, booking real
# da mesma empresa. Por isso os testes que confirmam ações usam
# `company_factory` e gravam o booking de verdade (sem enfraquecer a FK).
real_repository = RuntimeRepository()


def _real_booking(company_id: int, contact_id: str, starts_at: str, ends_at: str) -> int:
    return real_repository.create_appointment_booking(
        provider="mock",
        title="Consultoria inicial",
        starts_at=starts_at,
        ends_at=ends_at,
        timezone="America/Sao_Paulo",
        company_id=company_id,
        contact_id=contact_id,
    )

COMPANY_CONTEXT = {
    "enabled_modules": {
        "scheduling": True,
        "reminders": True,
    },
    "business_hours": {
        "timezone": "America/Sao_Paulo",
        "scheduling_window": "Agendamentos com antecedência mínima de 2 horas.",
    },
    "catalog": {
        "items": [
            {
                "name": "Consultoria inicial",
                "schedule_required": True,
                "duration": "60 minutos",
            }
        ]
    },
    "scheduling_policy": {
        "enabled": True,
        "minimum_notice_minutes": 120,
        "default_slot_duration_minutes": 60,
    },
    "reminder_policy": {
        "enabled": True,
    },
}


class FakeRepository:
    def __init__(self) -> None:
        self.flow = None
        self.closed_flow = None
        self.bookings = []
        self.contacts = []

    def get_active_conversation_flow(self, contact_id: str, company_id: int | None = None):
        return self.flow

    def upsert_conversation_flow(self, **kwargs):
        self.flow = {
            "id": 11,
            "contact_id": kwargs["contact_id"],
            "company_id": kwargs["company_id"],
            "flow_type": kwargs["flow_type"],
            "status": kwargs.get("status", "active"),
            "state": kwargs["state"],
        }
        return 11

    def close_conversation_flow(self, flow_id: int, *, status: str = "completed", state=None):
        self.closed_flow = {
            "flow_id": flow_id,
            "status": status,
            "state": state,
        }
        if self.flow:
            self.flow["status"] = status
            self.flow["state"] = state or self.flow["state"]

    def get_appointment_bookings(self, company_id: int | None = None, contact_id: str | None = None, status: str | None = None):
        bookings = list(self.bookings)
        if company_id is not None:
            bookings = [booking for booking in bookings if booking.get("company_id") == company_id]
        if contact_id is not None:
            bookings = [booking for booking in bookings if booking.get("contact_id") == contact_id]
        if status is not None:
            bookings = [booking for booking in bookings if booking.get("status") == status]
        return bookings

    def get_appointment_booking(self, booking_id: int, *, company_id: int | None = None):
        return next(
            (
                booking
                for booking in self.bookings
                if int(booking["id"]) == int(booking_id) and (company_id is None or booking.get("company_id") == company_id)
            ),
            None,
        )

    def search_contacts(self, *, query: str, company_id: int | None = None, limit: int = 10):
        normalized_query = query.lower()
        matches = [
            contact
            for contact in self.contacts
            if normalized_query in str(contact.get("contact_name") or "").lower()
            or normalized_query in str(contact.get("contact_id") or "").lower()
        ]
        return matches[:limit]

def test_scheduling_workflow_books_after_confirmation(monkeypatch, company_factory) -> None:
    fake_repository = FakeRepository()
    created_booking = {}
    company_id = company_factory("wf-schedule")

    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)
    monkeypatch.setattr(
        workflow_service,
        "_get_current_time",
        lambda timezone_obj: workflow_service.datetime.fromisoformat("2026-04-07T08:00:00-03:00").astimezone(timezone_obj),
    )
    monkeypatch.setattr(
        workflow_service,
        "query_calendar_availability",
        lambda **kwargs: {
            "slots": [
                {
                    "starts_at": "2026-04-08T10:00:00-03:00",
                    "ends_at": "2026-04-08T11:00:00-03:00",
                    "timezone": "America/Sao_Paulo",
                    "duration_minutes": 60,
                }
            ]
        },
    )

    def fake_create_calendar_booking(**kwargs):
        created_booking.update(kwargs)
        booking_id = _real_booking(
            company_id, kwargs["contact_id"], "2026-04-08T13:00:00+00:00", "2026-04-08T14:00:00+00:00"
        )
        created_booking["booking_id"] = booking_id
        return {
            "id": booking_id,
            "starts_at": "2026-04-08T13:00:00+00:00",
            "ends_at": "2026-04-08T14:00:00+00:00",
            "provider": "mock",
        }

    monkeypatch.setattr(workflow_service, "create_calendar_booking", fake_create_calendar_booking)

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="Quero agendar a Consultoria inicial amanhã às 10h",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )

    assert first.handled is True
    assert first.classification == "agendamento"
    assert "Tenho esse horário disponível" in first.reply
    assert fake_repository.flow is not None

    second = workflow_service.handle_conversation_workflow(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="sim, pode confirmar",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )

    assert second.handled is True
    assert second.action == "schedule_confirmed"
    assert second.metadata["booking_id"] == created_booking["booking_id"]
    assert created_booking["contact_id"] == "5511999999999"
    assert "Cliente Teste" in created_booking["title"]
    assert fake_repository.closed_flow["status"] == "completed"


def test_customer_message_for_reminder_is_not_handled_as_customer_workflow(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)

    result = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="Me lembra amanhã de falar com você",
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert result.handled is False


def test_catalog_question_does_not_start_scheduling_workflow(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)

    result = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="Quais serviços vocês oferecem e quanto custa a consulta inicial?",
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert result.handled is False
    assert fake_repository.flow is None


def test_service_request_without_booking_verb_can_start_scheduling(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)

    result = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="Quero uma consulta inicial",
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert result.handled is True
    assert result.classification == "agendamento"
    assert fake_repository.flow is not None


def test_queue_offer_response_is_handled_before_regular_scheduling(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(
        workflow_service,
        "process_queue_contact_message",
        lambda **kwargs: {
            "reply": "Perfeito. Vou te manter na fila de prioridade.",
            "action": "queue_membership_confirmed",
            "workflow_type": "appointment_queue",
            "metadata": {"queue_entry_id": 12},
        },
    )

    result = workflow_service.handle_conversation_workflow(
        contact_id="5511777777777",
        contact_name="Cliente Teste",
        message="sim, pode me manter na fila",
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert result.handled is True
    assert result.action == "queue_membership_confirmed"
    assert result.workflow_type == "appointment_queue"


def test_customer_can_cancel_booking_via_natural_conversation(monkeypatch, company_factory) -> None:
    company_id = company_factory("wf-cancel")
    fake_repository = FakeRepository()
    fake_repository.bookings = [
        {
            "id": _real_booking(company_id, "5511999999999", "2030-04-08T13:00:00+00:00", "2030-04-08T14:00:00+00:00"),
            "company_id": company_id,
            "contact_id": "5511999999999",
            "title": "Consultoria inicial",
            "starts_at": "2030-04-08T13:00:00+00:00",
            "ends_at": "2030-04-08T14:00:00+00:00",
            "status": "confirmed",
            "timezone": "America/Sao_Paulo",
            "scheduling_profile_key": "novos",
            "customer_profile": "new_customer",
        }
    ]
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)
    monkeypatch.setattr(
        workflow_service,
        "_get_current_time",
        lambda timezone_obj: workflow_service.datetime.fromisoformat("2030-04-07T08:00:00-03:00").astimezone(timezone_obj),
    )
    monkeypatch.setattr(
        workflow_service,
        "release_booking_slot",
        # `company_id` agora é repassado: o cancelamento fica no escopo da empresa.
        lambda booking_id, company_id=None: {
            "booking": {"id": booking_id, "status": "cancelled"},
            "queue_recovery": {"created_offer": None},
        },
    )

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="Oi, preciso cancelar meu agendamento",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )
    assert first.handled is True
    assert first.action == "booking_cancel_confirmation_requested"

    second = workflow_service.handle_conversation_workflow(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="sim, pode cancelar",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )
    assert second.handled is True
    assert second.action == "booking_cancelled"


def test_customer_can_reschedule_booking_via_natural_conversation(monkeypatch, company_factory) -> None:
    company_id = company_factory("wf-reschedule")
    fake_repository = FakeRepository()
    fake_repository.bookings = [
        {
            "id": _real_booking(company_id, "5511888888888", "2030-04-08T13:00:00+00:00", "2030-04-08T14:00:00+00:00"),
            "company_id": company_id,
            "contact_id": "5511888888888",
            "title": "Consultoria inicial",
            "description": "Consulta original",
            "starts_at": "2030-04-08T13:00:00+00:00",
            "ends_at": "2030-04-08T14:00:00+00:00",
            "status": "confirmed",
            "timezone": "America/Sao_Paulo",
            "scheduling_profile_key": "novos",
            "customer_profile": "new_customer",
        }
    ]
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)
    monkeypatch.setattr(
        workflow_service,
        "_get_current_time",
        lambda timezone_obj: workflow_service.datetime.fromisoformat("2030-04-07T08:00:00-03:00").astimezone(timezone_obj),
    )
    monkeypatch.setattr(
        workflow_service,
        "query_calendar_availability",
        lambda **kwargs: {
            "slots": [
                {
                    "starts_at": "2030-04-09T15:00:00-03:00",
                    "ends_at": "2030-04-09T16:00:00-03:00",
                    "timezone": "America/Sao_Paulo",
                    "duration_minutes": 60,
                }
            ]
        },
    )
    monkeypatch.setattr(
        workflow_service,
        "reschedule_calendar_booking",
        lambda **kwargs: {
            "id": kwargs["booking_id"],
            "starts_at": "2030-04-09T18:00:00+00:00",
            "ends_at": "2030-04-09T19:00:00+00:00",
        },
    )

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="Quero remarcar meu horário para amanhã às 15h",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )
    assert first.handled is True
    assert first.action == "booking_reschedule_confirmation_requested"

    second = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="sim",
        company_id=company_id,
        company_context=COMPANY_CONTEXT,
    )
    assert second.handled is True
    assert second.action == "booking_rescheduled"


def test_operator_can_start_booking_flow_for_existing_customer(monkeypatch, company_factory) -> None:
    company_id = company_factory("wf-operator")
    fake_repository = FakeRepository()
    fake_repository.contacts = [
        {
            "contact_id": "5511777777777",
            "contact_name": "Maria Silva",
            "customer_profile": "returning_customer",
        }
    ]
    operator_context = {
        **COMPANY_CONTEXT,
        "internal_operation": {
            "operator_contact_ids": ["5511666666666"],
        },
    }
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)
    monkeypatch.setattr(
        workflow_service,
        "_extract_operator_booking_command",
        lambda message: {
            "intent": "schedule_for_customer",
            "customer_reference": "Maria Silva",
            "service_name": "Consultoria inicial",
            "customer_profile": None,
            "reason": "Teste",
        },
    )
    monkeypatch.setattr(
        workflow_service,
        "_get_current_time",
        lambda timezone_obj: workflow_service.datetime.fromisoformat("2030-04-07T08:00:00-03:00").astimezone(timezone_obj),
    )
    monkeypatch.setattr(
        workflow_service,
        "query_calendar_availability",
        lambda **kwargs: {
            "slots": [
                {
                    "starts_at": "2030-04-08T10:00:00-03:00",
                    "ends_at": "2030-04-08T11:00:00-03:00",
                    "timezone": "America/Sao_Paulo",
                    "duration_minutes": 60,
                }
            ]
        },
    )
    monkeypatch.setattr(
        workflow_service,
        "create_calendar_booking",
        lambda **kwargs: {
            "id": _real_booking(
                company_id, kwargs["contact_id"], "2030-04-08T13:00:00+00:00", "2030-04-08T14:00:00+00:00"
            ),
            "starts_at": "2030-04-08T13:00:00+00:00",
            "ends_at": "2030-04-08T14:00:00+00:00",
        },
    )

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511666666666",
        contact_name="Dono",
        message="Marque o cliente Maria Silva amanhã às 10h para consultoria inicial",
        company_id=company_id,
        company_context=operator_context,
    )
    assert first.handled is True
    assert first.action == "operator_customer_confirmation_requested"

    second = workflow_service.handle_conversation_workflow(
        contact_id="5511666666666",
        contact_name="Dono",
        message="sim",
        company_id=company_id,
        company_context=operator_context,
    )
    assert second.handled is True
    assert second.action == "operator_booking_confirmation_requested"

    third = workflow_service.handle_conversation_workflow(
        contact_id="5511666666666",
        contact_name="Dono",
        message="sim, pode confirmar",
        company_id=company_id,
        company_context=operator_context,
    )
    assert third.handled is True
    assert third.action == "operator_booking_created"
    assert third.metadata["contact_id"] == "5511777777777"


def test_operator_flow_requires_disambiguation_when_multiple_contacts_match(monkeypatch) -> None:
    fake_repository = FakeRepository()
    fake_repository.contacts = [
        {
            "contact_id": "5511777777777",
            "contact_name": "Maria Silva",
            "customer_profile": "returning_customer",
        },
        {
            "contact_id": "5511888888888",
            "contact_name": "Maria Silva Santos",
            "customer_profile": "new_customer",
        },
    ]
    operator_context = {
        **COMPANY_CONTEXT,
        "internal_operation": {
            "operator_contact_ids": ["5511666666666"],
        },
    }
    monkeypatch.setattr(workflow_service, "repository", fake_repository)
    monkeypatch.setattr(workflow_service, "process_queue_contact_message", lambda **kwargs: None)
    monkeypatch.setattr(
        workflow_service,
        "_extract_operator_booking_command",
        lambda message: {
            "intent": "schedule_for_customer",
            "customer_reference": "Maria Silva",
            "service_name": "Consultoria inicial",
            "customer_profile": None,
            "reason": "Teste",
        },
    )

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511666666666",
        contact_name="Dono",
        message="Marque a Maria Silva amanhã às 10h",
        company_id=1,
        company_context=operator_context,
    )

    assert first.handled is True
    assert first.action == "operator_customer_selection_requested"
    assert "Maria Silva | 5511777777777" in first.reply
    assert "Maria Silva Santos | 5511888888888" in first.reply
