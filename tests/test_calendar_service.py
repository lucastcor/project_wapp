from app.core.config import settings
from app.services import calendar_service


def test_query_calendar_availability_respects_local_conflicts(monkeypatch) -> None:
    class FakeRepository:
        def get_active_calendar_integration(self, company_id=None):
            return None

        def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
            return [
                {
                    "id": 1,
                    "starts_at": "2026-04-07T13:00:00+00:00",
                    "ends_at": "2026-04-07T14:00:00+00:00",
                    "timezone": "America/Sao_Paulo",
                    "status": "confirmed",
                }
            ]

    monkeypatch.setattr(calendar_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(settings, "calendar_timezone", "America/Sao_Paulo")
    monkeypatch.setattr(settings, "calendar_default_workday_start", "09:00")
    monkeypatch.setattr(settings, "calendar_default_workday_end", "12:00")
    monkeypatch.setattr(settings, "calendar_slot_increment_minutes", 60)
    monkeypatch.setattr(
        calendar_service,
        "get_company_context",
        lambda company_slug=None: {
            "business_hours": {
                "timezone": "America/Sao_Paulo",
            },
            "scheduling_policy": {},
        },
    )

    result = calendar_service.query_calendar_availability(
        company_id=1,
        start_date="2026-04-07",
        end_date="2026-04-07",
        duration_minutes=60,
        limit=5,
    )

    slots = result["slots"]
    assert result["provider"] == "mock"
    assert len(slots) == 2
    assert slots[0]["starts_at"].startswith("2026-04-07T09:00:00")
    assert slots[1]["starts_at"].startswith("2026-04-07T11:00:00")


def test_create_calendar_booking_persists_mock_booking(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self):
            self.created = None

        def get_active_calendar_integration(self, company_id=None):
            return None

        def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
            return []

        def create_appointment_booking(self, **kwargs):
            self.created = kwargs
            return 10

        def get_appointment_bookings(self, company_id=None):
            return [
                {
                    "id": 10,
                    "company_id": company_id,
                    "contact_id": "contato-1",
                    "integration_id": None,
                    "provider": "mock",
                    "external_event_id": None,
                    "title": "Consultoria inicial",
                    "description": "Detalhes",
                    "starts_at": "2026-04-08T13:00:00+00:00",
                    "ends_at": "2026-04-08T14:00:00+00:00",
                    "timezone": "America/Sao_Paulo",
                    "status": "confirmed",
                    "created_at": "2026-04-06T12:00:00+00:00",
                    "updated_at": "2026-04-06T12:00:00+00:00",
                }
            ]

    fake_repository = FakeRepository()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "schedule_booking_customer_reminders", lambda booking: [])
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(settings, "calendar_timezone", "America/Sao_Paulo")
    monkeypatch.setattr(
        calendar_service,
        "get_company_context",
        lambda company_slug=None: {
            "business_hours": {
                "timezone": "America/Sao_Paulo",
            },
            "scheduling_policy": {},
        },
    )

    booking = calendar_service.create_calendar_booking(
        company_id=1,
        contact_id="contato-1",
        title="Consultoria inicial",
        starts_at="2026-04-08T10:00:00-03:00",
        ends_at="2026-04-08T11:00:00-03:00",
        timezone_name="America/Sao_Paulo",
        description="Detalhes",
    )

    assert booking["id"] == 10
    assert fake_repository.created["provider"] == "mock"
    assert fake_repository.created["contact_id"] == "contato-1"
    assert fake_repository.created["title"] == "Consultoria inicial"
    assert booking["scheduled_reminders"] == []


def test_query_calendar_availability_respects_company_business_hours(monkeypatch) -> None:
    class FakeRepository:
        def get_company(self, company_id: int):
            return {"id": company_id, "slug": "empresa-teste"}

        def get_active_calendar_integration(self, company_id=None):
            return None

        def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
            return []

    monkeypatch.setattr(calendar_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(
        calendar_service,
        "get_company_context",
        lambda company_slug=None: {
            "business_hours": {
                "timezone": "America/Sao_Paulo",
                "weekdays": "10:00-12:00",
                "saturday": "fechado",
                "sunday": "fechado",
            },
            "scheduling_policy": {
                "slot_increment_minutes": 30,
            },
        },
    )

    result = calendar_service.query_calendar_availability(
        company_id=1,
        start_date="2026-04-06",
        end_date="2026-04-06",
        duration_minutes=60,
        limit=10,
    )

    slots = result["slots"]
    assert len(slots) == 3
    assert slots[0]["starts_at"].startswith("2026-04-06T10:00:00")
    assert slots[-1]["starts_at"].startswith("2026-04-06T11:00:00")
