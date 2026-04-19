from app.services import reminder_service


def test_dispatch_due_reminders_filters_by_audience_and_marks_sent(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.created_outbound = []
            self.marked = []

        def get_due_customer_reminders(self, reference_time: str):
            return [
                {
                    "id": 5,
                    "company_id": 1,
                    "contact_id": "5511999999999",
                    "reminder_text": "Lembrete do agendamento",
                    "reminder_summary": "Lembrete do agendamento",
                    "audience": "customer",
                },
                {
                    "id": 6,
                    "company_id": 1,
                    "contact_id": "5511888888888",
                    "reminder_text": "Lembrar de ligar",
                    "reminder_summary": "Lembrete interno: ligar",
                    "audience": "internal",
                },
            ]

        def create_outbound_message(self, **kwargs):
            self.created_outbound.append(kwargs)
            return 19 + len(self.created_outbound)

        def mark_customer_reminder_sent(self, reminder_id: int, *, outbound_message_id: int | None = None):
            self.marked.append((reminder_id, outbound_message_id))

    fake_repository = FakeRepository()
    monkeypatch.setattr(reminder_service, "repository", fake_repository)

    results = reminder_service.dispatch_due_reminders(audience="customer")

    assert len(results) == 1
    assert results[0]["audience"] == "customer"
    assert fake_repository.created_outbound[0]["source"] == "reminder_customer"
    assert fake_repository.marked == [(5, 20)]


def test_create_internal_reminder_from_natural_text(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.created = None

        def get_company(self, company_id: int):
            return {"id": company_id, "slug": "empresa-teste"}

        def create_customer_reminder(self, **kwargs):
            self.created = kwargs
            return 31

        def get_customer_reminder(self, reminder_id: int):
            return {
                "id": reminder_id,
                **(self.created or {}),
            }

    fake_repository = FakeRepository()
    monkeypatch.setattr(reminder_service, "repository", fake_repository)
    monkeypatch.setattr(
        reminder_service,
        "get_company_context",
        lambda company_slug=None: {
            "business_hours": {"timezone": "America/Sao_Paulo"},
            "reminder_policy": {
                "enabled": True,
                "internal_manual_reminders_enabled": True,
            },
        },
    )

    reminder = reminder_service.create_internal_reminder(
        company_id=1,
        owner_contact_id="funcionario-1",
        message_text="Me lembra amanhã às 9h de ligar para o cliente João",
        timezone_name="America/Sao_Paulo",
    )

    assert reminder["id"] == 31
    assert fake_repository.created["audience"] == "internal"
    assert fake_repository.created["source"] == "owner_manual"
    assert fake_repository.created["contact_id"] == "funcionario-1"


def test_schedule_booking_customer_reminders_from_policy(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.created = []

        def get_company(self, company_id: int):
            return {"id": company_id, "slug": "empresa-teste"}

        def get_customer_reminders_for_booking(self, appointment_booking_id: int, audience: str = "customer"):
            return []

        def create_customer_reminder(self, **kwargs):
            self.created.append(kwargs)
            return len(self.created)

        def get_customer_reminder(self, reminder_id: int):
            return {"id": reminder_id, **self.created[reminder_id - 1]}

    fake_repository = FakeRepository()
    monkeypatch.setattr(reminder_service, "repository", fake_repository)
    monkeypatch.setattr(
        reminder_service,
        "get_company_context",
        lambda company_slug=None: {
            "company_name": "Empresa Teste",
            "business_hours": {"timezone": "America/Sao_Paulo"},
            "reminder_policy": {
                "enabled": True,
                "appointment_customer_reminders_enabled": True,
                "customer_reminder_offsets_days": [5, 1],
            },
        },
    )

    reminders = reminder_service.schedule_booking_customer_reminders(
        {
            "id": 7,
            "company_id": 1,
            "contact_id": "5511999999999",
            "title": "Consultoria inicial",
            "starts_at": "2030-04-10T13:00:00+00:00",
            "timezone": "America/Sao_Paulo",
        }
    )

    assert len(reminders) == 2
    assert fake_repository.created[0]["audience"] == "customer"
    assert fake_repository.created[0]["source"] == "appointment_policy"
    assert fake_repository.created[0]["appointment_booking_id"] == 7


def test_dispatch_due_reminders_uses_company_policy_channel(monkeypatch) -> None:
    dispatched = []

    class FakeRepository:
        def get_due_customer_reminders(self, reference_time: str):
            return [
                {
                    "id": 8,
                    "company_id": 1,
                    "contact_id": "5511999999999",
                    "reminder_text": "Lembrete do agendamento",
                    "reminder_summary": "Lembrete do agendamento",
                    "audience": "customer",
                }
            ]

        def create_outbound_message(self, **kwargs):
            return 44

        def mark_customer_reminder_sent(self, reminder_id: int, *, outbound_message_id: int | None = None):
            return None

        def get_company(self, company_id: int):
            return {"id": company_id, "slug": "empresa-teste"}

    monkeypatch.setattr(reminder_service, "repository", FakeRepository())
    monkeypatch.setattr(
        reminder_service,
        "get_company_context",
        lambda company_slug=None: {
            "business_hours": {"timezone": "America/Sao_Paulo"},
            "reminder_policy": {
                "enabled": True,
                "default_channel": "whatsapp",
                "customer_default_channel": "whatsapp",
            },
        },
    )
    monkeypatch.setattr(
        reminder_service,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: dispatched.append(
            {
                "outbound_message_id": outbound_message_id,
                "channel": channel,
                "company_id": company_id,
            }
        ) or {
            "outbound_message_id": outbound_message_id,
            "channel": channel,
            "company_id": company_id,
        },
    )

    results = reminder_service.dispatch_due_reminders(dispatch_immediately=True, audience="customer")

    assert results[0]["channel"] == "whatsapp"
    assert dispatched == [
        {
            "outbound_message_id": 44,
            "channel": "whatsapp",
            "company_id": 1,
        }
    ]
