from app.services import conversation_workflow_service as workflow_service


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

def test_scheduling_workflow_books_after_confirmation(monkeypatch) -> None:
    fake_repository = FakeRepository()
    created_booking = {}

    monkeypatch.setattr(workflow_service, "repository", fake_repository)
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
        return {
            "id": 77,
            "starts_at": "2026-04-08T13:00:00+00:00",
            "ends_at": "2026-04-08T14:00:00+00:00",
            "provider": "mock",
        }

    monkeypatch.setattr(workflow_service, "create_calendar_booking", fake_create_calendar_booking)

    first = workflow_service.handle_conversation_workflow(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="Quero agendar a Consultoria inicial amanhã às 10h",
        company_id=1,
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
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert second.handled is True
    assert second.action == "schedule_confirmed"
    assert second.metadata["booking_id"] == 77
    assert created_booking["contact_id"] == "5511999999999"
    assert "Cliente Teste" in created_booking["title"]
    assert fake_repository.closed_flow["status"] == "completed"


def test_customer_message_for_reminder_is_not_handled_as_customer_workflow(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(workflow_service, "repository", fake_repository)

    result = workflow_service.handle_conversation_workflow(
        contact_id="5511888888888",
        contact_name="Cliente Teste",
        message="Me lembra amanhã de falar com você",
        company_id=1,
        company_context=COMPANY_CONTEXT,
    )

    assert result.handled is False
