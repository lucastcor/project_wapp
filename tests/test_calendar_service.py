from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.services import appointment_queue_service as queue_service
from app.services import calendar_service
from app.services.company_policy_service import BookingChangeRule, BookingRules
from app.storage.migrations_booking_capacity import BookingCapacityConflictError

FIXED_NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
PERMISSIVE_RULE = BookingChangeRule(allowed=True, min_notice_minutes=0, outside_window="handoff")


def _patch_policy(monkeypatch, *, rules: BookingRules | None = None, now: datetime = FIXED_NOW) -> None:
    resolved = rules or BookingRules(cancellation=PERMISSIVE_RULE, reschedule=PERMISSIVE_RULE)
    monkeypatch.setattr(calendar_service, "_load_booking_rules", lambda company_id: resolved)
    monkeypatch.setattr(calendar_service, "_utcnow", lambda: now)


def _patch_open_context(monkeypatch) -> None:
    monkeypatch.setattr(
        calendar_service,
        "get_runtime_company_context",
        lambda company_id=None: {
            "business_hours": {"timezone": "America/Sao_Paulo", "weekdays": "08:00-20:00"},
            "scheduling_policy": {},
        },
    )


def test_google_calendar_readiness_rejects_placeholder_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_calendar_id", "primary")
    monkeypatch.setattr(settings, "google_calendar_client_id", "change-me-google-client-id")
    monkeypatch.setattr(settings, "google_calendar_client_secret", "change-me-google-client-secret")
    monkeypatch.setattr(settings, "google_calendar_refresh_token", "change-me-google-refresh-token")

    provider = calendar_service.GoogleCalendarProvider(
        {
            "provider": "google_calendar",
            "config": {},
        }
    )

    readiness = provider.readiness()

    assert readiness["ready"] is False
    assert readiness["details"] == "Faltam credenciais reais do Google Calendar."


def test_create_calendar_integration_returns_scoped_integration(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.integration_lookup_calls: list[dict] = []

        def create_calendar_integration(self, **kwargs):
            assert kwargs["company_id"] == 9
            return 41

        def get_calendar_integration(self, integration_id: int, *, company_id=None):
            self.integration_lookup_calls.append(
                {
                    "integration_id": integration_id,
                    "company_id": company_id,
                }
            )
            return {
                "id": integration_id,
                "company_id": company_id,
                "provider": "mock",
                "active": 1,
                "config": {"label": "Agenda Empresa 9"},
            }

    fake_repository = FakeRepository()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)

    result = calendar_service.create_calendar_integration(
        provider="mock",
        company_id=9,
        config={"label": "Agenda Empresa 9"},
    )

    assert result["id"] == 41
    assert result["company_id"] == 9
    assert fake_repository.integration_lookup_calls == [
        {
            "integration_id": 41,
            "company_id": 9,
        }
    ]


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
        "get_runtime_company_context",
        lambda company_id=None: {
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


def test_query_calendar_availability_treats_reschedule_hold_as_busy(monkeypatch) -> None:
    class FakeRepository:
        def get_active_calendar_integration(self, company_id=None):
            return None

        def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
            return [
                {
                    "id": 1,
                    "starts_at": "2026-04-06T12:00:00+00:00",
                    "ends_at": "2026-04-06T13:00:00+00:00",
                    "hold_starts_at": "2026-04-07T13:00:00+00:00",
                    "hold_ends_at": "2026-04-07T14:00:00+00:00",
                    "timezone": "America/Sao_Paulo",
                    "status": "confirmed",
                }
            ]

    monkeypatch.setattr(calendar_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(
        calendar_service,
        "get_runtime_company_context",
        lambda company_id=None: {
            "business_hours": {"timezone": "America/Sao_Paulo", "weekdays": "09:00-12:00"},
            "scheduling_policy": {"slot_increment_minutes": 60},
        },
    )

    result = calendar_service.query_calendar_availability(
        company_id=1,
        start_date="2026-04-07",
        end_date="2026-04-07",
        duration_minutes=60,
    )

    assert [slot["starts_at"][11:16] for slot in result["slots"]] == ["09:00", "11:00"]


def test_create_calendar_booking_persists_mock_booking(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self):
            self.created = None
            self.confirmed = None

        def get_active_calendar_integration(self, company_id=None):
            return None

        def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
            return []

        def create_appointment_booking(self, **kwargs):
            self.created = kwargs
            return 10

        def confirm_appointment_booking_reservation(self, booking_id, *, external_event_id, company_id=None):
            self.confirmed = (booking_id, external_event_id, company_id)
            return True

        def get_appointment_booking(self, booking_id, *, company_id=None):
            return {
                "id": booking_id,
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

    fake_repository = FakeRepository()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "schedule_booking_customer_reminders", lambda booking: [])
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(settings, "calendar_timezone", "America/Sao_Paulo")
    monkeypatch.setattr(
        calendar_service,
        "get_runtime_company_context",
        lambda company_id=None: {
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
    # Reserva de capacidade antes do efeito externo, persistida em UTC (A09).
    assert fake_repository.created["status"] == "pending"
    assert fake_repository.created["starts_at"] == "2026-04-08T13:00:00+00:00"
    assert fake_repository.confirmed == (10, None, 1)
    assert booking["scheduled_reminders"] == []


class _ReservationRepository:
    """Repositório falso que registra a ordem entre reserva e efeito externo."""

    def __init__(self, calls: list[str], *, conflict: bool = False) -> None:
        self.calls = calls
        self.conflict = conflict

    def get_active_calendar_integration(self, company_id=None):
        return {"id": 3, "company_id": company_id, "provider": "google_calendar", "config": {}, "active": 1}

    def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
        return []

    def create_appointment_booking(self, **kwargs):
        self.calls.append(f"reserve:{kwargs['status']}")
        if self.conflict:
            raise BookingCapacityConflictError("occupied")
        return 77

    def release_appointment_booking_reservation(self, booking_id, *, company_id=None):
        self.calls.append(f"release:{booking_id}")
        return True

    def confirm_appointment_booking_reservation(self, booking_id, *, external_event_id, company_id=None):
        self.calls.append("confirm")
        return True


class _FailingProvider:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def readiness(self):
        return {"provider": "google_calendar", "ready": True, "details": "ok"}

    def list_busy_intervals(self, window_start, window_end):
        return []

    def create_external_event(self, **kwargs):
        self.calls.append("provider:create")
        raise calendar_service.CalendarProviderError("provider unavailable")


def _patch_create_dependencies(monkeypatch, repository, provider, refunds: list) -> None:
    monkeypatch.setattr(calendar_service, "repository", repository)
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: provider)
    monkeypatch.setattr(calendar_service, "require_usage_capacity", lambda **kwargs: {"usage_record_id": 5})
    monkeypatch.setattr(
        calendar_service,
        "refund_usage_reservation",
        lambda *, company_id, decision: refunds.append(decision),
    )
    _patch_open_context(monkeypatch)


def test_create_reserves_before_provider_and_releases_capacity_when_provider_fails(monkeypatch) -> None:
    calls: list[str] = []
    refunds: list = []
    _patch_create_dependencies(monkeypatch, _ReservationRepository(calls), _FailingProvider(calls), refunds)

    with pytest.raises(calendar_service.CalendarProviderError, match="provider unavailable"):
        calendar_service.create_calendar_booking(
            company_id=4,
            contact_id="contato-x",
            title="Consulta",
            starts_at="2026-04-08T10:00:00-03:00",
            ends_at="2026-04-08T11:00:00-03:00",
        )

    assert calls == ["reserve:pending", "provider:create", "release:77"]
    assert refunds == [{"usage_record_id": 5}]


def test_create_maps_database_capacity_conflict_without_external_effect(monkeypatch) -> None:
    calls: list[str] = []
    refunds: list = []
    _patch_create_dependencies(
        monkeypatch,
        _ReservationRepository(calls, conflict=True),
        _FailingProvider(calls),
        refunds,
    )

    with pytest.raises(calendar_service.BookingConflictError):
        calendar_service.create_calendar_booking(
            company_id=4,
            contact_id="contato-x",
            title="Consulta",
            starts_at="2026-04-08T10:00:00-03:00",
            ends_at="2026-04-08T11:00:00-03:00",
        )

    assert calls == ["reserve:pending"]
    assert refunds == [{"usage_record_id": 5}]


def test_cancel_calendar_booking_syncs_external_event(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.booking = {
                "id": 9,
                "company_id": 1,
                "contact_id": "cliente-1",
                "provider": "google_calendar",
                "external_event_id": "evt-123",
                "title": "Consulta",
                "description": "Detalhes",
                "starts_at": "2026-04-09T13:00:00+00:00",
                "ends_at": "2026-04-09T14:00:00+00:00",
                "timezone": "America/Sao_Paulo",
                "status": "confirmed",
            }

        def get_company(self, company_id: int):
            return {"id": company_id, "slug": "empresa-teste"}

        def get_appointment_booking(self, booking_id: int, *, company_id=None):
            return self.booking if booking_id == 9 else None

        def get_active_calendar_integration(self, company_id=None):
            return {"id": 1, "company_id": company_id, "provider": "google_calendar", "config": {}, "active": 1}

        def update_appointment_booking_status(
            self,
            booking_id: int,
            status: str,
            *,
            company_id=None,
        ) -> None:
            self.booking["status"] = status

        def cancel_customer_reminders_for_booking(
            self,
            appointment_booking_id: int,
            *,
            company_id=None,
        ) -> int:
            return 2

    class FakeProvider:
        def __init__(self) -> None:
            self.cancelled_event_id = None

        def readiness(self):
            return {"provider": "google_calendar", "ready": True, "details": "ok"}

        def cancel_external_event(self, *, external_event_id: str) -> bool:
            self.cancelled_event_id = external_event_id
            return True

    fake_repository = FakeRepository()
    fake_provider = FakeProvider()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: fake_provider)
    _patch_policy(monkeypatch)

    result = calendar_service.cancel_calendar_booking(booking_id=9)

    assert result["booking"]["status"] == "cancelled"
    assert result["cancelled_reminders"] == 2
    assert result["external_sync"]["status"] == "cancelled"
    assert fake_provider.cancelled_event_id == "evt-123"
    assert result["policy_decision"] == {"outcome": "allowed", "reason": "within_policy", "actor": "customer"}


class _PolicyRepository:
    def __init__(self, starts_at: datetime) -> None:
        self.booking = {
            "id": 31,
            "company_id": 6,
            "contact_id": "cliente-politica",
            "provider": "mock",
            "external_event_id": None,
            "title": "Consulta",
            "description": None,
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
            "timezone": "America/Sao_Paulo",
            "status": "confirmed",
        }
        self.status_updates: list[str] = []

    def get_appointment_booking(self, booking_id: int, *, company_id=None):
        if booking_id != 31 or company_id not in (None, 6):
            return None
        return self.booking

    def get_active_calendar_integration(self, company_id=None):
        return None

    def update_appointment_booking_status(self, booking_id: int, status: str, *, company_id=None) -> None:
        self.status_updates.append(status)
        self.booking["status"] = status

    def cancel_customer_reminders_for_booking(self, appointment_booking_id: int, *, company_id=None) -> int:
        return 0


def test_cancellation_policy_requires_human_for_customer_and_automation_but_not_operator(monkeypatch) -> None:
    fake_repository = _PolicyRepository(FIXED_NOW + timedelta(hours=2))
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    notice_rule = BookingChangeRule(allowed=True, min_notice_minutes=24 * 60, outside_window="handoff")
    _patch_policy(monkeypatch, rules=BookingRules(cancellation=notice_rule, reschedule=notice_rule))

    for actor in ("customer", "automation"):
        with pytest.raises(calendar_service.BookingPolicyError) as blocked:
            calendar_service.cancel_calendar_booking(booking_id=31, company_id=6, actor=actor)
        assert (blocked.value.outcome, blocked.value.reason) == ("requires_human", "inside_minimum_notice")
        assert "cliente-politica" not in str(blocked.value)
    assert fake_repository.status_updates == []

    result = calendar_service.cancel_calendar_booking(booking_id=31, company_id=6, actor="operator")

    assert fake_repository.status_updates == ["cancelled"]
    assert result["policy_decision"] == {
        "outcome": "requires_human",
        "reason": "inside_minimum_notice",
        "actor": "operator",
    }


def test_denied_policy_blocks_every_actor_and_unknown_actor_is_rejected(monkeypatch) -> None:
    fake_repository = _PolicyRepository(FIXED_NOW + timedelta(hours=2))
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    deny_rule = BookingChangeRule(allowed=False, min_notice_minutes=0, outside_window="deny")
    _patch_policy(monkeypatch, rules=BookingRules(cancellation=deny_rule, reschedule=deny_rule))

    for actor in ("customer", "automation", "operator"):
        with pytest.raises(calendar_service.BookingPolicyError) as blocked:
            calendar_service.cancel_calendar_booking(booking_id=31, company_id=6, actor=actor)
        assert (blocked.value.outcome, blocked.value.reason) == ("denied", "change_not_allowed")
    with pytest.raises(calendar_service.BookingPolicyError):
        calendar_service.reschedule_calendar_booking(
            booking_id=31,
            company_id=6,
            starts_at="2026-04-10T10:00:00-03:00",
            ends_at="2026-04-10T11:00:00-03:00",
            actor="operator",
        )
    with pytest.raises(calendar_service.CalendarProviderError, match="actor must be one of"):
        calendar_service.cancel_calendar_booking(booking_id=31, company_id=6, actor="bot")
    assert fake_repository.status_updates == []


def test_booking_of_another_company_is_not_found(monkeypatch) -> None:
    fake_repository = _PolicyRepository(FIXED_NOW + timedelta(days=3))
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    _patch_policy(monkeypatch)

    with pytest.raises(calendar_service.BookingNotFoundError):
        calendar_service.cancel_calendar_booking(booking_id=31, company_id=7, actor="operator")
    with pytest.raises(calendar_service.BookingNotFoundError):
        calendar_service.reschedule_calendar_booking(
            booking_id=31,
            company_id=7,
            starts_at="2026-04-10T10:00:00-03:00",
            ends_at="2026-04-10T11:00:00-03:00",
            actor="operator",
        )


def test_release_booking_slot_forwards_actor_to_cancellation(monkeypatch) -> None:
    received: list[dict] = []

    def fake_cancel(**kwargs):
        received.append(kwargs)
        return {"booking": {"id": kwargs["booking_id"]}, "cancelled_reminders": 0, "external_sync": {}}

    monkeypatch.setattr(calendar_service, "cancel_calendar_booking", fake_cancel)
    monkeypatch.setattr(queue_service, "offer_released_booking_slot", lambda **kwargs: {"created_offer": None})

    queue_service.release_booking_slot(booking_id=5, company_id=2, actor="operator")
    queue_service.release_booking_slot(booking_id=6, company_id=2)

    assert received == [
        {"booking_id": 5, "company_id": 2, "actor": "operator"},
        {"booking_id": 6, "company_id": 2},
    ]


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
        "get_runtime_company_context",
        lambda company_id=None: {
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


def test_query_calendar_availability_respects_scheduling_profile_windows(monkeypatch) -> None:
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
        "get_runtime_company_context",
        lambda company_id=None: {
            "business_hours": {
                "timezone": "America/Sao_Paulo",
                "weekdays": "09:00-18:00",
            },
            "scheduling_policy": {
                "slot_increment_minutes": 30,
                "default_profile_key": "novos",
                "profiles": [
                    {
                        "key": "novos",
                        "label": "Clientes novos",
                        "allowed_weekdays": ["monday"],
                        "day_windows": {
                            "monday": "14:00-16:00",
                        },
                    }
                ],
            },
        },
    )

    result = calendar_service.query_calendar_availability(
        company_id=1,
        start_date="2026-04-06",
        end_date="2026-04-06",
        duration_minutes=60,
        limit=10,
        scheduling_profile_key="novos",
    )

    slots = result["slots"]
    assert [slot["starts_at"][11:16] for slot in slots] == ["14:00", "14:30", "15:00"]
    assert result["scheduling_profile"]["key"] == "novos"


class _RescheduleRepository:
    def __init__(self) -> None:
        self.booking = {
            "id": 11,
            "company_id": 1,
            "contact_id": "cliente-2",
            "provider": "google_calendar",
            "external_event_id": "evt-555",
            "scheduling_profile_key": "retorno",
            "customer_profile": "returning_customer",
            "title": "Retorno",
            "description": "Cliente de retorno",
            "starts_at": "2026-04-14T13:00:00+00:00",
            "ends_at": "2026-04-14T14:00:00+00:00",
            "timezone": "America/Sao_Paulo",
            "status": "confirmed",
        }
        self.calls: list[str] = []
        self.hold_conflict = False

    def get_company(self, company_id: int):
        return {"id": company_id, "slug": "empresa-teste"}

    def get_appointment_booking(self, booking_id: int, *, company_id=None):
        return self.booking if booking_id == 11 else None

    def get_active_calendar_integration(self, company_id=None):
        return {"id": 1, "company_id": company_id, "provider": "google_calendar", "config": {}, "active": 1}

    def get_appointment_bookings_between(self, starts_at: str, ends_at: str, company_id=None):
        return [self.booking]

    def hold_appointment_booking_interval(self, booking_id, *, hold_starts_at, hold_ends_at, company_id=None):
        self.calls.append(f"hold:{hold_starts_at}")
        if self.hold_conflict:
            raise BookingCapacityConflictError("occupied")
        return True

    def clear_appointment_booking_hold(self, booking_id, *, company_id=None) -> None:
        self.calls.append("clear_hold")

    def update_appointment_booking_schedule(self, booking_id: int, **kwargs) -> bool:
        # Compare-and-set (revisão P03): True quando o agendamento ainda estava
        # ativo e com o hold desta remarcação.
        self.calls.append("update_schedule")
        kwargs.pop("company_id", None)
        self.booking.update(kwargs)
        return True

    def cancel_customer_reminders_for_booking(
        self,
        appointment_booking_id: int,
        *,
        company_id=None,
    ) -> int:
        return 3


def _patch_reschedule_context(monkeypatch) -> None:
    monkeypatch.setattr(
        calendar_service,
        "get_runtime_company_context",
        lambda company_id=None: {
            "business_hours": {
                "timezone": "America/Sao_Paulo",
                "weekdays": "09:00-18:00",
            },
            "scheduling_policy": {
                "profiles": [
                    {
                        "key": "retorno",
                        "label": "Retorno",
                        "allowed_weekdays": ["wednesday"],
                        "day_windows": {"wednesday": "14:00-18:00"},
                    }
                ]
            },
        },
    )


def test_reschedule_calendar_booking_updates_existing_booking_and_provider(monkeypatch) -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.updated_kwargs = None

        def readiness(self):
            return {"provider": "google_calendar", "ready": True, "details": "ok"}

        def list_busy_intervals(self, window_start, window_end):
            # A07: remarcação revalida a ocupação externa antes do efeito.
            return []

        def update_external_event(self, **kwargs):
            fake_repository.calls.append("provider:update")
            self.updated_kwargs = kwargs
            return kwargs["external_event_id"]

    fake_repository = _RescheduleRepository()
    fake_provider = FakeProvider()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: fake_provider)
    monkeypatch.setattr(calendar_service, "schedule_booking_customer_reminders", lambda booking: [{"id": 1}])
    _patch_reschedule_context(monkeypatch)
    _patch_policy(monkeypatch)

    result = calendar_service.reschedule_calendar_booking(
        booking_id=11,
        starts_at="2026-04-15T14:00:00-03:00",
        ends_at="2026-04-15T15:00:00-03:00",
        scheduling_profile_key="retorno",
    )

    assert result["id"] == 11
    assert fake_provider.updated_kwargs["external_event_id"] == "evt-555"
    assert fake_repository.booking["starts_at"] == "2026-04-15T17:00:00+00:00"
    assert result["cancelled_reminders"] == 3
    assert result["scheduled_reminders"] == [{"id": 1}]
    # O novo intervalo é retido (em UTC) antes do efeito externo.
    assert fake_repository.calls == ["hold:2026-04-15T17:00:00+00:00", "provider:update", "update_schedule"]


def test_reschedule_provider_failure_releases_hold_and_keeps_original(monkeypatch) -> None:
    class FailingProvider:
        def readiness(self):
            return {"provider": "google_calendar", "ready": True, "details": "ok"}

        def list_busy_intervals(self, window_start, window_end):
            return []

        def update_external_event(self, **kwargs):
            fake_repository.calls.append("provider:update")
            raise calendar_service.CalendarProviderError("provider unavailable")

    fake_repository = _RescheduleRepository()
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: FailingProvider())
    _patch_reschedule_context(monkeypatch)
    _patch_policy(monkeypatch)

    with pytest.raises(calendar_service.CalendarProviderError, match="provider unavailable"):
        calendar_service.reschedule_calendar_booking(
            booking_id=11,
            starts_at="2026-04-15T14:00:00-03:00",
            ends_at="2026-04-15T15:00:00-03:00",
            scheduling_profile_key="retorno",
        )

    assert fake_repository.calls == ["hold:2026-04-15T17:00:00+00:00", "provider:update", "clear_hold"]
    assert fake_repository.booking["starts_at"] == "2026-04-14T13:00:00+00:00"


def test_reschedule_into_occupied_interval_conflicts_before_provider(monkeypatch) -> None:
    class UnexpectedProvider:
        def readiness(self):
            return {"provider": "google_calendar", "ready": True, "details": "ok"}

        def list_busy_intervals(self, window_start, window_end):
            return []

        def update_external_event(self, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("provider must not be called on a capacity conflict")

    fake_repository = _RescheduleRepository()
    fake_repository.hold_conflict = True
    monkeypatch.setattr(calendar_service, "repository", fake_repository)
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: UnexpectedProvider())
    _patch_reschedule_context(monkeypatch)
    _patch_policy(monkeypatch)

    with pytest.raises(calendar_service.BookingConflictError):
        calendar_service.reschedule_calendar_booking(
            booking_id=11,
            starts_at="2026-04-15T14:00:00-03:00",
            ends_at="2026-04-15T15:00:00-03:00",
            scheduling_profile_key="retorno",
        )

    assert fake_repository.calls == ["hold:2026-04-15T17:00:00+00:00"]
