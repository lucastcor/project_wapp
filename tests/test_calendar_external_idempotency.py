"""P04 - idempotência com a agenda externa.

ID de evento estável no Google (nova tentativa recupera em vez de duplicar),
exclusão já realizada distinta de falta de permissão, timeout que não devolve
a vaga, reconciliação de reserva `pending` e A07 (ocupação externa revalidada
na remarcação). Sem rede: service do Google e provider substituídos.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime, timedelta

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.services import calendar_service
from app.services.calendar_service import (
    GOOGLE_EVENT_MARKER,
    BookingConflictError,
    BusyInterval,
    CalendarProviderError,
    ExternalCalendarUncertainError,
    GoogleCalendarProvider,
    stable_event_key,
)
from app.services.company_config_service import save_company_config
from app.storage.db import db_transaction
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
TZ_NAME = "America/Sao_Paulo"
# Quarta-feira, 10:00 em São Paulo.
BASE = datetime(2027, 3, 10, 13, 0, tzinfo=UTC)


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


class _Call:
    def __init__(self, fn) -> None:
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGoogleEvents:
    def __init__(self, *, delete_status: int | None = None) -> None:
        self.events: dict[str, dict] = {}
        self.insert_calls = 0
        self.delete_status = delete_status

    def insert(self, calendarId, body):
        def run():
            self.insert_calls += 1
            if body.get("id") in self.events:
                raise _http_error(409)
            self.events[body["id"]] = {**body, "status": "confirmed"}
            return {"id": body["id"]}

        return _Call(run)

    def get(self, calendarId, eventId):
        def run():
            if eventId not in self.events:
                raise _http_error(404)
            return self.events[eventId]

        return _Call(run)

    def delete(self, calendarId, eventId):
        def run():
            if self.delete_status:
                raise _http_error(self.delete_status)
            self.events.pop(eventId, None)
            return ""

        return _Call(run)


class FakeGoogleCalendars:
    def __init__(self, *, accessible: bool = True) -> None:
        self.accessible = accessible

    def get(self, calendarId):
        def run():
            if not self.accessible:
                raise _http_error(404)
            return {"id": calendarId}

        return _Call(run)


def _google(events: FakeGoogleEvents, *, calendar_accessible: bool = True) -> GoogleCalendarProvider:
    provider = GoogleCalendarProvider({"provider": "google_calendar", "config": {}})
    calendars = FakeGoogleCalendars(accessible=calendar_accessible)
    service = type("Service", (), {"events": lambda self: events, "calendars": lambda self: calendars})()
    provider._get_google_service = lambda: (service, "calendar-test")  # type: ignore[method-assign]
    return provider


def _own_event(key: str, status: str = "confirmed") -> dict:
    return {"id": key, "status": status, "extendedProperties": {"private": {GOOGLE_EVENT_MARKER: key}}}


def test_stable_event_key_is_a_valid_google_event_id() -> None:
    key = stable_event_key(12, 345)

    assert re.fullmatch(r"[0-9a-v]{5,1024}", key)
    assert stable_event_key(12, 345) == key
    assert stable_event_key(13, 345) != key


def test_google_create_retry_with_the_same_key_returns_the_existing_event() -> None:
    events = FakeGoogleEvents()
    provider = _google(events)
    key = stable_event_key(7, 42)
    window = {"title": "Consulta", "starts_at": BASE, "ends_at": BASE + timedelta(hours=1), "timezone_name": TZ_NAME}

    first = provider.create_external_event(**window, event_key=key)
    retry = provider.create_external_event(**window, event_key=key)

    assert first == retry == key
    assert events.insert_calls == 2
    assert list(events.events) == [key]


@pytest.mark.parametrize("status", [404, 410])
def test_google_cancel_of_an_already_deleted_event_is_not_an_error(status: int) -> None:
    provider = _google(FakeGoogleEvents(delete_status=status))

    assert provider.cancel_external_event(external_event_id="ap1b1") is False


@pytest.mark.parametrize("status", [401, 403])
def test_google_cancel_without_permission_stays_an_error(status: int) -> None:
    provider = _google(FakeGoogleEvents(delete_status=status))

    with pytest.raises(CalendarProviderError):
        provider.cancel_external_event(external_event_id="ap1b1")


def test_google_find_reports_existing_missing_and_cancelled_events() -> None:
    events = FakeGoogleEvents()
    provider = _google(events)
    events.events["ap1b1"] = _own_event("ap1b1")
    events.events["ap1b2"] = _own_event("ap1b2", status="cancelled")

    assert provider.find_external_event(event_key="ap1b1") == (True, "ap1b1")
    assert provider.find_external_event(event_key="ap1b2") == (False, None)
    assert provider.find_external_event(event_key="ap1b9") == (False, None)


def test_google_event_with_the_same_key_from_another_environment_is_not_ours() -> None:
    events = FakeGoogleEvents()
    provider = _google(events)
    key = stable_event_key(7, 42)
    events.events[key] = {"id": key, "status": "confirmed"}
    window = {"title": "Consulta", "starts_at": BASE, "ends_at": BASE + timedelta(hours=1), "timezone_name": TZ_NAME}

    with pytest.raises(CalendarProviderError):
        provider.create_external_event(**window, event_key=key)
    assert provider.find_external_event(event_key=key) == (None, None)


def test_google_404_on_an_inaccessible_calendar_is_not_an_absent_event() -> None:
    provider = _google(FakeGoogleEvents(delete_status=404), calendar_accessible=False)

    with pytest.raises(CalendarProviderError):
        provider.cancel_external_event(external_event_id="ap1b1")
    assert provider.find_external_event(event_key="ap1b1") == (None, None)


# ── serviço de agenda com provider roteirizado ───────────────────────


class ScriptedProvider(calendar_service.BaseCalendarProvider):
    provider_name = "google_calendar"

    def __init__(self, *, create_error: Exception | None = None, find_map: dict | None = None) -> None:
        super().__init__({"provider": "google_calendar", "config": {}})
        self.create_error = create_error
        self.update_error: Exception | None = None
        self.on_create = None
        self.find_map = find_map or {}
        self.busy: list[BusyInterval] = []
        self.created_keys: list[str | None] = []
        self.cancelled_ids: list[str] = []

    def readiness(self):
        return {"provider": self.provider_name, "ready": True, "details": "ok"}

    def list_busy_intervals(self, window_start, window_end):
        return list(self.busy)

    def create_external_event(self, **kwargs):
        self.created_keys.append(kwargs.get("event_key"))
        if self.create_error is not None:
            raise self.create_error
        if self.on_create is not None:
            self.on_create(kwargs.get("event_key"))
        return kwargs.get("event_key")

    def cancel_external_event(self, *, external_event_id):
        self.cancelled_ids.append(external_event_id)
        return True

    def update_external_event(self, **kwargs):
        if self.update_error is not None:
            raise self.update_error
        return kwargs.get("external_event_id") or "evt-updated"

    def find_external_event(self, *, event_key):
        return self.find_map.get(event_key, (None, None))


@pytest.fixture
def agenda_company(company_factory, monkeypatch) -> int:
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(calendar_service, "require_usage_capacity", lambda **kwargs: {"usage_record_id": None})
    monkeypatch.setattr(calendar_service, "refund_usage_reservation", lambda **kwargs: None)
    monkeypatch.setattr(calendar_service, "schedule_booking_customer_reminders", lambda booking: [])
    monkeypatch.setattr(calendar_service, "emit_appointment_booked", lambda **kwargs: None)
    company_id = company_factory("external", business_type="services")
    save_company_config(company_id=company_id, context=copy.deepcopy(get_seed_company_context(None)), author="test")
    return company_id


def _use_provider(monkeypatch, provider: ScriptedProvider) -> None:
    monkeypatch.setattr(
        calendar_service,
        "_resolve_calendar_integration",
        lambda company_id=None: {
            "integration_id": None,
            "company_id": company_id,
            "provider": "google_calendar",
            "external_calendar_id": None,
            "config": {},
            "active": True,
            "source": "test",
        },
    )
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: provider)


def _create(company_id: int, offset_minutes: int = 0) -> dict:
    starts_at = BASE + timedelta(minutes=offset_minutes)
    return calendar_service.create_calendar_booking(
        company_id=company_id,
        contact_id=f"contact-{offset_minutes}",
        title="Consulta",
        starts_at=starts_at.isoformat(),
        ends_at=(starts_at + timedelta(hours=1)).isoformat(),
        timezone_name=TZ_NAME,
    )


def _bookings(company_id: int, status: str) -> list[dict]:
    return [booking for booking in repository.get_appointment_bookings(company_id=company_id) if booking["status"] == status]


def test_timeout_keeps_the_reservation_with_its_stable_key(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider(create_error=TimeoutError("read timed out"))
    _use_provider(monkeypatch, provider)

    with pytest.raises(CalendarProviderError):
        _create(agenda_company)

    pending = _bookings(agenda_company, "pending")
    assert len(pending) == 1
    assert provider.created_keys == [stable_event_key(agenda_company, int(pending[0]["id"]))]


def test_explicit_provider_refusal_releases_the_reservation(agenda_company, monkeypatch) -> None:
    _use_provider(monkeypatch, ScriptedProvider(create_error=CalendarProviderError("HTTP 400")))

    with pytest.raises(CalendarProviderError):
        _create(agenda_company)

    assert _bookings(agenda_company, "pending") == []


def test_reconciliation_confirms_existing_releases_missing_and_keeps_unknown(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider(create_error=TimeoutError("read timed out"))
    _use_provider(monkeypatch, provider)
    for offset in (0, 120, 240):
        with pytest.raises(CalendarProviderError):
            _create(agenda_company, offset)
    pending_ids = sorted(int(booking["id"]) for booking in _bookings(agenda_company, "pending"))
    assert len(pending_ids) == 3
    with db_transaction() as connection:
        for booking_id in pending_ids:
            connection.cursor().execute(
                "UPDATE appointment_bookings SET created_at = ? WHERE id = ?",
                ("2000-01-01 00:00:00+00:00", booking_id),
            )
    exists_id, missing_id, unknown_id = pending_ids
    provider.find_map = {
        stable_event_key(agenda_company, exists_id): (True, "evt-exists"),
        stable_event_key(agenda_company, missing_id): (False, None),
    }

    results = calendar_service.reconcile_pending_reservations(older_than_minutes=15)

    actions = {item["booking_id"]: item["action"] for item in results}
    assert actions[exists_id] == "pending_confirmed_from_external"
    assert actions[missing_id] == "pending_released"
    assert actions[unknown_id] == "pending_needs_review"
    confirmed = repository.get_appointment_booking(exists_id, company_id=agenda_company)
    assert (confirmed["status"], confirmed["external_event_id"]) == ("confirmed", "evt-exists")
    assert repository.get_appointment_booking(missing_id, company_id=agenda_company) is None
    assert repository.get_appointment_booking(unknown_id, company_id=agenda_company)["status"] == "pending"


def test_reschedule_revalidates_external_busy_time_but_ignores_its_own_event(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)
    booking = _create(agenda_company, 0)
    own_start, own_end = BASE, BASE + timedelta(hours=1)
    target_start = BASE + timedelta(hours=3)
    target_end = target_start + timedelta(hours=1)

    provider.busy = [BusyInterval(starts_at=target_start, ends_at=target_end)]
    with pytest.raises(BookingConflictError):
        calendar_service.reschedule_calendar_booking(
            booking_id=int(booking["id"]),
            starts_at=target_start.isoformat(),
            ends_at=target_end.isoformat(),
            timezone_name=TZ_NAME,
            company_id=agenda_company,
        )
    unchanged = repository.get_appointment_booking(int(booking["id"]), company_id=agenda_company)
    assert datetime.fromisoformat(str(unchanged["starts_at"])).astimezone(UTC) == own_start
    assert unchanged["hold_starts_at"] is None

    overlapping_own = BASE + timedelta(minutes=30)
    provider.busy = [BusyInterval(starts_at=own_start, ends_at=own_end)]
    rescheduled = calendar_service.reschedule_calendar_booking(
        booking_id=int(booking["id"]),
        starts_at=overlapping_own.isoformat(),
        ends_at=(overlapping_own + timedelta(hours=1)).isoformat(),
        timezone_name=TZ_NAME,
        company_id=agenda_company,
    )
    assert datetime.fromisoformat(str(rescheduled["starts_at"])).astimezone(UTC) == overlapping_own


# ── revisão P04: reserva `pending` não deixa evento órfão ────────────


def _pending_after_timeout(company_id: int, provider: ScriptedProvider, offset_minutes: int = 0) -> int:
    provider.create_error = TimeoutError("read timed out")
    with pytest.raises(ExternalCalendarUncertainError):
        _create(company_id, offset_minutes)
    provider.create_error = None
    pending = _bookings(company_id, "pending")
    assert len(pending) == 1
    return int(pending[0]["id"])


def test_cancelling_a_pending_booking_removes_the_event_found_by_its_key(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)
    booking_id = _pending_after_timeout(agenda_company, provider)
    key = stable_event_key(agenda_company, booking_id)
    provider.find_map = {key: (True, key)}

    result = calendar_service.cancel_calendar_booking(booking_id=booking_id, company_id=agenda_company)

    assert provider.cancelled_ids == [key]
    assert result["external_sync"]["status"] == "cancelled"
    assert repository.get_appointment_booking(booking_id, company_id=agenda_company)["status"] == "cancelled"


def test_pending_booking_with_unknown_external_state_is_not_cancelled_or_rescheduled(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)
    booking_id = _pending_after_timeout(agenda_company, provider)
    target = BASE + timedelta(hours=4)

    with pytest.raises(ExternalCalendarUncertainError):
        calendar_service.cancel_calendar_booking(booking_id=booking_id, company_id=agenda_company)
    with pytest.raises(ExternalCalendarUncertainError):
        calendar_service.reschedule_calendar_booking(
            booking_id=booking_id,
            starts_at=target.isoformat(),
            ends_at=(target + timedelta(hours=1)).isoformat(),
            timezone_name=TZ_NAME,
            company_id=agenda_company,
        )

    stored = repository.get_appointment_booking(booking_id, company_id=agenda_company)
    assert (stored["status"], stored["hold_starts_at"]) == ("pending", None)
    assert provider.cancelled_ids == []


def test_event_created_for_a_booking_cancelled_mid_call_is_removed(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)

    def cancel_during_call(event_key: str) -> None:
        booking_id = int(event_key.rsplit("b", 1)[1])
        repository.update_appointment_booking_status(booking_id, "cancelled", company_id=agenda_company)

    provider.on_create = cancel_during_call
    with pytest.raises(CalendarProviderError):
        _create(agenda_company)

    assert provider.cancelled_ids == provider.created_keys


def test_reconciliation_does_not_ask_a_different_integration(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)
    booking_id = _pending_after_timeout(agenda_company, provider)
    with db_transaction() as connection:
        connection.cursor().execute(
            "UPDATE appointment_bookings SET created_at = ? WHERE id = ?", ("2000-01-01 00:00:00+00:00", booking_id)
        )
    provider.find_map = {stable_event_key(agenda_company, booking_id): (False, None)}
    monkeypatch.setattr(
        calendar_service,
        "_resolve_calendar_integration",
        lambda company_id=None: {"integration_id": None, "company_id": company_id, "provider": "mock", "config": {}},
    )

    results = calendar_service.reconcile_pending_reservations(older_than_minutes=15)

    assert {"booking_id": booking_id, "action": "pending_needs_review"} in results
    assert repository.get_appointment_booking(booking_id, company_id=agenda_company)["status"] == "pending"


def test_reschedule_without_external_answer_keeps_the_new_interval_held(agenda_company, monkeypatch) -> None:
    provider = ScriptedProvider()
    _use_provider(monkeypatch, provider)
    booking = _create(agenda_company, 0)
    target = BASE + timedelta(hours=5)
    reschedule = {
        "booking_id": int(booking["id"]),
        "starts_at": target.isoformat(),
        "ends_at": (target + timedelta(hours=1)).isoformat(),
        "timezone_name": TZ_NAME,
        "company_id": agenda_company,
    }

    provider.update_error = CalendarProviderError("HTTP 400")
    with pytest.raises(CalendarProviderError):
        calendar_service.reschedule_calendar_booking(**reschedule)
    assert repository.get_appointment_booking(int(booking["id"]), company_id=agenda_company)["hold_starts_at"] is None

    provider.update_error = TimeoutError("read timed out")
    with pytest.raises(ExternalCalendarUncertainError):
        calendar_service.reschedule_calendar_booking(**reschedule)
    held = repository.get_appointment_booking(int(booking["id"]), company_id=agenda_company)
    assert held["hold_starts_at"] is not None
    assert datetime.fromisoformat(str(held["starts_at"])).astimezone(UTC) == BASE
