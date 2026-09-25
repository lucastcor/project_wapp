"""P03 — capacidade e concorrência da agenda (A05, A06, A08, A09).

Rodam no backend da suíte. O aceite exige PostgreSQL real
(`TEST_DATABASE_URL=postgresql://...`): lá a guarda é a constraint de exclusão
e as threads usam conexões separadas do pool. No SQLite os mesmos cenários
exercitam os triggers equivalentes. Testes que só fazem sentido no PostgreSQL
são pulados com motivo explícito fora dele.
"""
from __future__ import annotations

import copy
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.company_context import get_seed_company_context
from app.core.config import settings
from app.main import app
from app.services import calendar_service
from app.services.company_config_service import save_company_config
from app.storage import db as db_module
from app.storage.db import db_session, db_transaction, get_database_backend
from app.storage.migrations_booking_capacity import (
    OVERLAP_CONSTRAINT,
    REPORT_COMMAND,
    BookingCapacityConflictError,
    BookingResourceUnavailableError,
    build_capacity_report,
)
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
client = TestClient(app)
POSTGRES = get_database_backend() == "postgres"
requires_postgres = pytest.mark.skipif(
    not POSTGRES,
    reason="Exige PostgreSQL real (TEST_DATABASE_URL=postgresql://...): aceite P03 pendente sem ele.",
)
FULL_MIGRATIONS = list(db_module.MIGRATIONS)
TZ_NAME = "America/Sao_Paulo"
# Quarta-feira, 10:00 em São Paulo.
BASE = datetime(2027, 3, 10, 13, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _slot(offset_minutes: int, duration_minutes: int = 60) -> tuple[str, str]:
    starts_at = BASE + timedelta(minutes=offset_minutes)
    return _iso(starts_at), _iso(starts_at + timedelta(minutes=duration_minutes))


def _contact() -> str:
    return f"test-contact-{uuid4().hex[:12]}"


def _book(
    company_id: int | None,
    offset_minutes: int = 0,
    duration_minutes: int = 60,
    *,
    status: str = "confirmed",
    resource_id: int | None = None,
    contact_id: str | None = None,
) -> int:
    starts_at, ends_at = _slot(offset_minutes, duration_minutes)
    return repository.create_appointment_booking(
        provider="mock",
        title="Consulta",
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=TZ_NAME,
        company_id=company_id,
        contact_id=contact_id or _contact(),
        status=status,
        resource_id=resource_id,
    )


def _active_bookings(company_id: int) -> list[dict[str, Any]]:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, status, resource_id, hold_starts_at
            FROM appointment_bookings
            WHERE company_id = ? AND status IN ('confirmed', 'pending')
            ORDER BY id
            """,
            (company_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def _execute(sql: str, params: tuple[Any, ...]) -> None:
    with db_transaction() as connection:
        connection.cursor().execute(sql, params)


def _is_integrity_error(error: BaseException) -> bool:
    return isinstance(error, sqlite3.IntegrityError) or str(getattr(error, "sqlstate", "") or "").startswith("23")


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value)).astimezone(UTC)


# ── recurso reservável ────────────────────────────────────────────────


def test_company_gets_one_default_resource_with_capacity_one(company_factory) -> None:
    company_id = company_factory("cap")

    first = repository.ensure_default_booking_resource(company_id)
    second = repository.ensure_default_booking_resource(company_id)
    booking_id = _book(company_id)

    assert first["id"] == second["id"]
    assert (first["resource_key"], first["capacity"], first["active"]) == ("default", 1, 1)
    assert [item["id"] for item in repository.list_booking_resources(company_id=company_id)] == [first["id"]]
    assert repository.get_appointment_booking(booking_id, company_id=company_id)["resource_id"] == first["id"]
    with pytest.raises(Exception) as capacity_two:
        _execute("UPDATE booking_resources SET capacity = 2 WHERE id = ?", (first["id"],))
    assert _is_integrity_error(capacity_two.value)


def test_new_company_booking_creates_default_resource_on_first_reservation(company_factory) -> None:
    company_id = company_factory("cap")
    assert repository.list_booking_resources(company_id=company_id) == []

    booking_id = _book(company_id)

    resources = repository.list_booking_resources(company_id=company_id)
    assert [item["resource_key"] for item in resources] == ["default"]
    assert repository.get_appointment_booking(booking_id, company_id=company_id)["resource_id"] == resources[0]["id"]


# ── A06: sobreposição, adjacência, recurso e empresa ───────────────────


def test_partial_overlap_conflicts_and_adjacent_intervals_are_allowed(company_factory) -> None:
    company_id = company_factory("cap")
    _book(company_id, 0, 60)

    for offset, duration in ((30, 60), (-30, 60), (15, 30), (-30, 120)):
        with pytest.raises(BookingCapacityConflictError):
            _book(company_id, offset, duration)

    _book(company_id, 60, 60)
    _book(company_id, -60, 60)
    assert len(_active_bookings(company_id)) == 3


def test_same_instant_written_with_another_offset_is_the_same_interval(company_factory) -> None:
    company_id = company_factory("cap")
    _book(company_id, 0, 60)  # 13:00-14:00 UTC

    with pytest.raises(BookingCapacityConflictError):
        repository.create_appointment_booking(
            provider="mock", title="Consulta", timezone=TZ_NAME, company_id=company_id,
            starts_at="2027-03-10T10:30:00-03:00", ends_at="2027-03-10T11:30:00-03:00",
        )
    # 11:00-12:00 em São Paulo começa exatamente quando a primeira termina.
    repository.create_appointment_booking(
        provider="mock", title="Consulta", timezone=TZ_NAME, company_id=company_id,
        starts_at="2027-03-10T11:00:00-03:00", ends_at="2027-03-10T12:00:00-03:00",
    )
    assert len(_active_bookings(company_id)) == 2


def test_distinct_resource_is_independent(company_factory) -> None:
    company_id = company_factory("cap")
    second_room = repository.create_booking_resource(company_id=company_id, resource_key="sala-2", label="Sala 2")

    _book(company_id, 0, 60)
    _book(company_id, 0, 60, resource_id=second_room["id"])
    with pytest.raises(BookingCapacityConflictError):
        _book(company_id, 30, 60, resource_id=second_room["id"])

    assert len(_active_bookings(company_id)) == 2


def test_same_interval_in_another_company_is_independent(company_factory) -> None:
    company_a = company_factory("cap")
    company_b = company_factory("cap")

    _book(company_a, 0, 60)
    _book(company_b, 0, 60)

    assert len(_active_bookings(company_a)) == len(_active_bookings(company_b)) == 1


def test_cancelled_booking_frees_capacity_and_reactivation_is_guarded(company_factory) -> None:
    company_id = company_factory("cap")
    first = _book(company_id, 0, 60)
    repository.update_appointment_booking_status(first, "cancelled", company_id=company_id)
    _book(company_id, 0, 60)

    with pytest.raises(BookingCapacityConflictError):
        repository.update_appointment_booking_status(first, "confirmed", company_id=company_id)

    assert repository.get_appointment_booking(first, company_id=company_id)["status"] == "cancelled"


def test_database_rejects_resource_of_another_company(company_factory) -> None:
    company_a = company_factory("cap")
    company_b = company_factory("cap")
    foreign = repository.ensure_default_booking_resource(company_b)
    own_booking = _book(company_a, 0, 60)
    starts_at, ends_at = _slot(240)

    insert_sql = (
        "INSERT INTO appointment_bookings (company_id, contact_id, resource_id, provider, title, "
        "starts_at, ends_at, timezone, status) VALUES (?, ?, ?, 'mock', 'Consulta', ?, ?, ?, 'confirmed')"
    )
    with pytest.raises(Exception) as cross_tenant_insert:
        _execute(insert_sql, (company_a, _contact(), foreign["id"], starts_at, ends_at, TZ_NAME))
    assert _is_integrity_error(cross_tenant_insert.value)
    with pytest.raises(Exception) as without_resource:
        _execute(insert_sql, (company_a, _contact(), None, starts_at, ends_at, TZ_NAME))
    assert _is_integrity_error(without_resource.value)
    with pytest.raises(Exception) as cross_tenant_update:
        _execute("UPDATE appointment_bookings SET resource_id = ? WHERE id = ?", (foreign["id"], own_booking))
    assert _is_integrity_error(cross_tenant_update.value)
    with pytest.raises(BookingResourceUnavailableError):
        _book(company_a, 240, 60, resource_id=foreign["id"])
    with pytest.raises(Exception) as resource_in_use:
        _execute(
            "DELETE FROM booking_resources WHERE id = ?",
            (repository.get_appointment_booking(own_booking, company_id=company_a)["resource_id"],),
        )
    assert _is_integrity_error(resource_in_use.value)
    assert len(_active_bookings(company_a)) == 1


def test_inactive_resource_and_invalid_interval_are_refused(company_factory) -> None:
    company_id = company_factory("cap")
    room = repository.create_booking_resource(company_id=company_id, resource_key="sala-inativa")
    assert repository.set_booking_resource_active(company_id=company_id, resource_key="sala-inativa", active=False)

    with pytest.raises(BookingResourceUnavailableError):
        _book(company_id, 0, 60, resource_id=room["id"])
    with pytest.raises(Exception) as empty_interval:
        _book(company_id, 0, 0)
    assert _is_integrity_error(empty_interval.value)


# ── A05: concorrência real (conexões separadas) ────────────────────────


def _race(workers: list) -> list[Any]:
    barrier = threading.Barrier(len(workers))

    def run(worker):
        barrier.wait(timeout=10)
        try:
            return worker()
        except Exception as exc:  # resultado do teste, não falha da thread
            return exc

    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        futures = [executor.submit(run, worker) for worker in workers]
        return [future.result(timeout=60) for future in futures]


def test_concurrent_confirmations_for_the_same_slot_keep_exactly_one(company_factory) -> None:
    company_id = company_factory("cap")
    repository.ensure_default_booking_resource(company_id)

    results = _race([lambda: _book(company_id, 0, 60) for _ in range(4)])

    assert sum(isinstance(item, int) for item in results) == 1
    assert all(isinstance(item, (int, BookingCapacityConflictError)) for item in results), results
    assert len(_active_bookings(company_id)) == 1


def test_concurrent_partial_overlaps_keep_exactly_one(company_factory) -> None:
    company_id = company_factory("cap")
    repository.ensure_default_booking_resource(company_id)

    results = _race([lambda offset=offset: _book(company_id, offset, 60) for offset in (0, 20, 40)])

    assert sum(isinstance(item, int) for item in results) == 1
    assert all(isinstance(item, (int, BookingCapacityConflictError)) for item in results), results
    assert len(_active_bookings(company_id)) == 1


# ── serviço e API sobre o banco real ───────────────────────────────────


def _publish_config(company_id: int, policies: dict[str, Any] | None = None) -> None:
    context = copy.deepcopy(get_seed_company_context(None) or {})
    context["business_hours"] = {
        "timezone": TZ_NAME,
        "weekdays": "07:00-21:00",
        "saturday": "07:00-21:00",
        "sunday": "07:00-21:00",
    }
    context["scheduling_policy"] = {"slot_increment_minutes": 30}
    save_company_config(
        company_id=company_id,
        context=context,
        policies=policies,
        author="test-p03",
        change_note="p03 capacity test",
    )


@pytest.fixture
def isolated_side_effects(monkeypatch):
    """Billing, lembretes e gatilhos fora do escopo da guarda de capacidade."""
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(calendar_service, "require_usage_capacity", lambda **kwargs: {"usage_record_id": None})
    monkeypatch.setattr(calendar_service, "refund_usage_reservation", lambda **kwargs: None)
    monkeypatch.setattr(calendar_service, "schedule_booking_customer_reminders", lambda booking: [])
    monkeypatch.setattr(calendar_service, "emit_appointment_booked", lambda **kwargs: None)


@pytest.fixture
def agenda_company(company_factory, isolated_side_effects) -> int:
    company_id = company_factory("cap")
    _publish_config(company_id)
    return company_id


def _create_via_service(company_id: int, offset_minutes: int, *, contact_id: str | None = None) -> dict[str, Any]:
    starts_at, ends_at = _slot(offset_minutes)
    return calendar_service.create_calendar_booking(
        company_id=company_id,
        contact_id=contact_id or _contact(),
        title="Consulta",
        starts_at=starts_at,
        ends_at=ends_at,
        timezone_name=TZ_NAME,
    )


def test_two_contacts_racing_through_the_service_get_one_booking(agenda_company, monkeypatch) -> None:
    both_checked_availability = threading.Barrier(2)
    external_events: list[str] = []

    class RacingProvider(calendar_service.MockCalendarProvider):
        def list_busy_intervals(self, window_start, window_end):
            # As duas threads já passaram pela checagem local de disponibilidade:
            # só a guarda do banco pode separar as confirmações.
            both_checked_availability.wait(timeout=10)
            return []

        def create_external_event(self, **kwargs):
            event_id = f"evt-{uuid4().hex[:8]}"
            external_events.append(event_id)
            return event_id

    monkeypatch.setattr(calendar_service, "_get_provider", RacingProvider)

    results = _race([lambda: _create_via_service(agenda_company, 0) for _ in range(2)])

    created = [item for item in results if isinstance(item, dict)]
    conflicts = [item for item in results if isinstance(item, calendar_service.BookingConflictError)]
    assert (len(created), len(conflicts)) == (1, 1), results
    assert created[0]["status"] == "confirmed"
    assert len(external_events) == 1
    assert [row["status"] for row in _active_bookings(agenda_company)] == ["confirmed"]


def test_provider_failure_releases_the_reserved_capacity(agenda_company, monkeypatch) -> None:
    class FailingProvider(calendar_service.MockCalendarProvider):
        def create_external_event(self, **kwargs):
            # A reserva existe durante a chamada externa e já foi commitada.
            assert [row["status"] for row in _active_bookings(agenda_company)] == ["pending"]
            raise calendar_service.CalendarProviderError("provider unavailable")

    monkeypatch.setattr(calendar_service, "_get_provider", FailingProvider)
    with pytest.raises(calendar_service.CalendarProviderError, match="provider unavailable"):
        _create_via_service(agenda_company, 0)
    assert _active_bookings(agenda_company) == []

    monkeypatch.setattr(calendar_service, "_get_provider", calendar_service.MockCalendarProvider)
    assert _create_via_service(agenda_company, 0)["status"] == "confirmed"


def test_reschedule_holds_both_intervals_until_the_provider_answers(agenda_company, monkeypatch) -> None:
    booking = _create_via_service(agenda_company, 0)
    observed: dict[str, Any] = {}

    class ObservingProvider(calendar_service.MockCalendarProvider):
        def update_external_event(self, **kwargs):
            # Sem transação aberta durante a "rede": outra conexão escreve já.
            for label, offset in (("old", 0), ("new", 120)):
                try:
                    _book(agenda_company, offset, 60)
                    observed[label] = "booked"
                except BookingCapacityConflictError:
                    observed[label] = "conflict"
            return kwargs.get("external_event_id")

    monkeypatch.setattr(calendar_service, "_get_provider", ObservingProvider)
    new_starts, new_ends = _slot(120)
    result = calendar_service.reschedule_calendar_booking(
        booking_id=int(booking["id"]),
        company_id=agenda_company,
        starts_at=new_starts,
        ends_at=new_ends,
        timezone_name=TZ_NAME,
    )

    assert observed == {"old": "conflict", "new": "conflict"}
    assert _as_utc(result["starts_at"]) == BASE + timedelta(minutes=120)
    assert result["hold_starts_at"] is None
    # O intervalo antigo foi liberado; o novo continua ocupado.
    _book(agenda_company, 0, 60)
    with pytest.raises(BookingCapacityConflictError):
        _book(agenda_company, 150, 60)


def test_reschedule_failure_keeps_original_and_frees_the_new_interval(agenda_company, monkeypatch) -> None:
    booking = _create_via_service(agenda_company, 0)

    class FailingProvider(calendar_service.MockCalendarProvider):
        def update_external_event(self, **kwargs):
            raise calendar_service.CalendarProviderError("provider unavailable")

    monkeypatch.setattr(calendar_service, "_get_provider", FailingProvider)
    new_starts, new_ends = _slot(120)
    with pytest.raises(calendar_service.CalendarProviderError):
        calendar_service.reschedule_calendar_booking(
            booking_id=int(booking["id"]), company_id=agenda_company,
            starts_at=new_starts, ends_at=new_ends, timezone_name=TZ_NAME,
        )

    current = repository.get_appointment_booking(int(booking["id"]), company_id=agenda_company)
    assert _as_utc(current["starts_at"]) == BASE
    assert current["hold_starts_at"] is None
    _book(agenda_company, 120, 60)
    with pytest.raises(BookingCapacityConflictError):
        _book(agenda_company, 30, 60)


def test_reschedule_overlapping_its_own_interval_and_guard_behind_the_services_back(
    agenda_company, monkeypatch
) -> None:
    booking = _create_via_service(agenda_company, 0)
    other = _book(agenda_company, 240, 60)
    monkeypatch.setattr(calendar_service, "_get_provider", calendar_service.MockCalendarProvider)

    shifted_starts, shifted_ends = _slot(30)
    shifted = calendar_service.reschedule_calendar_booking(
        booking_id=int(booking["id"]), company_id=agenda_company,
        starts_at=shifted_starts, ends_at=shifted_ends, timezone_name=TZ_NAME,
    )
    assert _as_utc(shifted["starts_at"]) == BASE + timedelta(minutes=30)

    # Sem a checagem local, quem recusa é o banco.
    monkeypatch.setattr(calendar_service, "_get_local_busy_intervals", lambda *args, **kwargs: [])
    taken_starts, taken_ends = _slot(270)
    with pytest.raises(calendar_service.BookingConflictError):
        calendar_service.reschedule_calendar_booking(
            booking_id=int(booking["id"]), company_id=agenda_company,
            starts_at=taken_starts, ends_at=taken_ends, timezone_name=TZ_NAME,
        )
    current = repository.get_appointment_booking(int(booking["id"]), company_id=agenda_company)
    assert _as_utc(current["starts_at"]) == BASE + timedelta(minutes=30)
    assert current["hold_starts_at"] is None
    assert repository.get_appointment_booking(other, company_id=agenda_company)["status"] == "confirmed"


def _book_starting_soon(company_id: int) -> int:
    starts_at = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2)
    return repository.create_appointment_booking(
        provider="mock", title="Consulta", timezone=TZ_NAME, company_id=company_id, contact_id=_contact(),
        starts_at=_iso(starts_at), ends_at=_iso(starts_at + timedelta(hours=1)),
    )


def test_published_policy_gates_changes_by_actor(company_factory, isolated_side_effects) -> None:
    company_id = company_factory("cap")
    _publish_config(
        company_id,
        policies={
            "booking_rules": {
                "cancellation": {"allowed": True, "min_notice_minutes": 1440, "outside_window": "handoff"},
                "reschedule": {"allowed": False, "outside_window": "deny"},
            }
        },
    )
    booking_id = _book_starting_soon(company_id)

    for actor in ("customer", "automation"):
        with pytest.raises(calendar_service.BookingPolicyError) as blocked:
            calendar_service.cancel_calendar_booking(booking_id=booking_id, company_id=company_id, actor=actor)
        assert (blocked.value.outcome, blocked.value.reason) == ("requires_human", "inside_minimum_notice")
    with pytest.raises(calendar_service.BookingPolicyError) as denied:
        calendar_service.reschedule_calendar_booking(
            booking_id=booking_id, company_id=company_id, actor="operator",
            starts_at=_slot(0)[0], ends_at=_slot(0)[1], timezone_name=TZ_NAME,
        )
    assert denied.value.outcome == "denied"
    assert repository.get_appointment_booking(booking_id, company_id=company_id)["status"] == "confirmed"

    result = calendar_service.cancel_calendar_booking(booking_id=booking_id, company_id=company_id, actor="operator")
    assert result["booking"]["status"] == "cancelled"
    assert result["policy_decision"]["outcome"] == "requires_human"


def test_admin_api_uses_the_same_guards(company_factory, agenda_company, monkeypatch) -> None:
    starts_at, ends_at = _slot(0)
    body = {"title": "Consulta", "starts_at": starts_at, "ends_at": ends_at, "timezone": TZ_NAME}

    created = client.post(
        "/admin/calendar/bookings", params={"company_id": agenda_company}, json={**body, "contact_id": _contact()}
    )
    assert created.status_code == 200, created.text
    booking_id = created.json()["id"]

    duplicate = client.post(
        "/admin/calendar/bookings", params={"company_id": agenda_company}, json={**body, "contact_id": _contact()}
    )
    assert duplicate.status_code == 409
    # Automação/fila chama o mesmo serviço e recebe a mesma recusa.
    with pytest.raises(calendar_service.BookingConflictError):
        _create_via_service(agenda_company, 30)

    other_company = company_factory("cap")
    foreign_cancel = client.post(
        f"/admin/calendar/bookings/{booking_id}/cancel",
        params={"company_id": other_company},
        json={"trigger_queue_recovery": False},
    )
    assert foreign_cancel.status_code == 404
    foreign_reschedule = client.post(
        f"/admin/calendar/bookings/{booking_id}/reschedule",
        params={"company_id": other_company},
        json={"starts_at": _slot(120)[0], "ends_at": _slot(120)[1], "timezone": TZ_NAME},
    )
    assert foreign_reschedule.status_code == 404
    assert repository.get_appointment_booking(booking_id, company_id=agenda_company)["status"] == "confirmed"

    _book(agenda_company, 240, 60)
    taken = client.post(
        f"/admin/calendar/bookings/{booking_id}/reschedule",
        params={"company_id": agenda_company},
        json={"starts_at": _slot(270)[0], "ends_at": _slot(270)[1], "timezone": TZ_NAME},
    )
    assert taken.status_code == 409

    moved = client.post(
        f"/admin/calendar/bookings/{booking_id}/reschedule",
        params={"company_id": agenda_company},
        json={"starts_at": _slot(480)[0], "ends_at": _slot(480)[1], "timezone": TZ_NAME},
    )
    assert moved.status_code == 200, moved.text
    assert _as_utc(moved.json()["starts_at"]) == BASE + timedelta(minutes=480)
    assert moved.json()["policy_decision"]["actor"] == "operator"


def test_admin_api_operator_passes_handoff_but_not_denial(company_factory, isolated_side_effects) -> None:
    handoff_company = company_factory("cap")
    _publish_config(
        handoff_company,
        policies={"booking_rules": {"cancellation": {"min_notice_minutes": 1440, "outside_window": "handoff"}}},
    )
    deny_company = company_factory("cap")
    _publish_config(
        deny_company,
        policies={"booking_rules": {"cancellation": {"allowed": False, "outside_window": "deny"}}},
    )
    handoff_booking = _book_starting_soon(handoff_company)
    denied_contact = _contact()
    denied_booking = repository.create_appointment_booking(
        provider="mock", title="Consulta", timezone=TZ_NAME, company_id=deny_company, contact_id=denied_contact,
        starts_at=_slot(0)[0], ends_at=_slot(0)[1],
    )

    allowed = client.post(
        f"/admin/calendar/bookings/{handoff_booking}/cancel",
        params={"company_id": handoff_company},
        json={"trigger_queue_recovery": False},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["booking"]["status"] == "cancelled"

    for trigger_queue_recovery in (False, True):
        refused = client.post(
            f"/admin/calendar/bookings/{denied_booking}/cancel",
            params={"company_id": deny_company},
            json={"trigger_queue_recovery": trigger_queue_recovery},
        )
        assert refused.status_code == 422, refused.text
        detail = refused.json()["detail"]
        assert (detail["outcome"], detail["reason"]) == ("denied", "change_not_allowed")
        assert denied_contact not in refused.text
    assert repository.get_appointment_booking(denied_booking, company_id=deny_company)["status"] == "confirmed"


# ── guarda instalada ───────────────────────────────────────────────────


@requires_postgres
def test_postgres_guard_is_an_exclusion_constraint_backed_by_btree_gist() -> None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 AS present FROM pg_extension WHERE extname = 'btree_gist'")
        assert cursor.fetchone() is not None
        cursor.execute(
            """
            SELECT contype, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conname = ? AND conrelid = 'appointment_bookings'::regclass
            """,
            (OVERLAP_CONSTRAINT,),
        )
        row = dict(cursor.fetchone())
    assert row["contype"] == "x"
    assert "tstzmultirange" in row["definition"]
    assert "'[)'" in row["definition"]


@pytest.mark.skipif(POSTGRES, reason="Triggers são a guarda equivalente só no SQLite.")
def test_sqlite_guard_is_installed_as_triggers() -> None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'appointment_bookings' "
            "AND name LIKE 'trg_appointment_bookings_capacity_%'"
        )
        names = sorted(dict(row)["name"] for row in cursor.fetchall())
    assert names == ["trg_appointment_bookings_capacity_insert", "trg_appointment_bookings_capacity_update"]


# ── upgrade com dados legados ──────────────────────────────────────────


@pytest.fixture
def legacy_database(tmp_path, monkeypatch):
    """Banco descartável migrado só até a 069 (schema anterior ao P03)."""
    original_url = settings.database_url
    drop_name: str | None = None
    if POSTGRES:
        import psycopg  # type: ignore
        from psycopg import sql  # type: ignore

        parsed = urlparse(original_url)
        drop_name = f"{parsed.path.lstrip('/')}_upg_{uuid4().hex[:8]}"
        try:
            with psycopg.connect(original_url, autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(drop_name)))
        except Exception as exc:
            pytest.skip(f"não foi possível criar banco PostgreSQL descartável: {type(exc).__name__}")
        target_url = urlunparse(parsed._replace(path=f"/{drop_name}"))
    else:
        target_url = f"sqlite:///{tmp_path / 'legacy-before-070.db'}"

    db_module.close_postgres_pool()
    monkeypatch.setattr(settings, "database_url", target_url)
    monkeypatch.setattr(db_module, "MIGRATIONS", [item for item in FULL_MIGRATIONS if item[0] < "070"])
    try:
        db_module.init_db()
        monkeypatch.setattr(db_module, "MIGRATIONS", FULL_MIGRATIONS)
        yield
    finally:
        db_module.close_postgres_pool()
        settings.database_url = original_url
        if drop_name is not None:
            import psycopg  # type: ignore
            from psycopg import sql  # type: ignore

            with psycopg.connect(original_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(drop_name)))


def _legacy_booking(company_id: int | None, offset_minutes: int, duration_minutes: int = 60, **fields: Any) -> int:
    starts_at, ends_at = _slot(offset_minutes, duration_minutes)
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO appointment_bookings (
                company_id, contact_id, provider, title, description, starts_at, ends_at, timezone, status
            )
            VALUES (?, ?, 'mock', ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                fields.get("contact_id", _contact()),
                fields.get("title", "Consulta"),
                fields.get("description"),
                starts_at,
                ends_at,
                TZ_NAME,
                fields.get("status", "confirmed"),
            ),
        )
        return int(cursor.lastrowid)


def _applied_versions() -> set[str]:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT version FROM schema_migrations")
        return {str(dict(row)["version"]) for row in cursor.fetchall()}


def _booking_snapshot() -> list[tuple[Any, ...]]:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT id, company_id, status, starts_at, ends_at FROM appointment_bookings ORDER BY id")
        return [tuple(dict(row).values()) for row in cursor.fetchall()]


def _resource_table_exists() -> bool:
    with db_session() as connection:
        cursor = connection.cursor()
        if POSTGRES:
            cursor.execute("SELECT to_regclass('booking_resources') IS NOT NULL AS present")
            return bool(dict(cursor.fetchone())["present"])
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'booking_resources'")
        return cursor.fetchone() is not None


def test_upgrade_assigns_the_default_resource_to_legacy_bookings(legacy_database) -> None:
    assert "070_booking_capacity" not in _applied_versions()
    with_bookings = repository.create_company(slug=f"legacy-a-{uuid4().hex[:8]}", name="Legacy A")
    with_integration = repository.create_company(slug=f"legacy-b-{uuid4().hex[:8]}", name="Legacy B")
    without_agenda = repository.create_company(slug=f"legacy-c-{uuid4().hex[:8]}", name="Legacy C")
    legacy_ids = [
        _legacy_booking(with_bookings, 0),
        _legacy_booking(with_bookings, 60),  # adjacente
        _legacy_booking(with_bookings, 30, status="cancelled"),  # sobrepõe, mas não ocupa
        _legacy_booking(with_bookings, 180, status="pending"),
    ]
    orphanless = [_legacy_booking(None, 0), _legacy_booking(None, 0)]
    repository.create_calendar_integration(company_id=with_integration, provider="mock")

    db_module.init_db()

    assert "070_booking_capacity" in _applied_versions()
    default_a = repository.get_booking_resource(company_id=with_bookings, resource_key="default")
    default_b = repository.get_booking_resource(company_id=with_integration, resource_key="default")
    assert default_a is not None and default_b is not None
    assert repository.list_booking_resources(company_id=without_agenda) == []
    assert {
        repository.get_appointment_booking(booking_id, company_id=with_bookings)["resource_id"]
        for booking_id in legacy_ids
    } == {default_a["id"]}
    assert {repository.get_appointment_booking(booking_id)["resource_id"] for booking_id in orphanless} == {None}

    with pytest.raises(BookingCapacityConflictError):
        _book(with_bookings, 90, 60)
    db_module.init_db()  # reexecução é no-op
    assert len(repository.list_booking_resources(company_id=with_bookings)) == 1


def test_upgrade_with_overlapping_legacy_bookings_fails_without_changing_data(legacy_database) -> None:
    overlapping = repository.create_company(slug=f"legacy-d-{uuid4().hex[:8]}", name="Legacy D")
    pending_overlap = repository.create_company(slug=f"legacy-e-{uuid4().hex[:8]}", name="Legacy E")
    broken_interval = repository.create_company(slug=f"legacy-f-{uuid4().hex[:8]}", name="Legacy F")
    secret_contact = f"5511{uuid4().int % 10**9:09d}"
    first = _legacy_booking(overlapping, 0, contact_id=secret_contact, title="Maria Sigilosa", description="nota privada")
    second = _legacy_booking(overlapping, 30)
    third = _legacy_booking(pending_overlap, 0, status="pending")
    fourth = _legacy_booking(pending_overlap, 0)
    empty = _legacy_booking(broken_interval, 0, 0)
    before = _booking_snapshot()

    with pytest.raises(RuntimeError) as blocked:
        db_module.init_db()

    message = str(blocked.value)
    assert f"company_id={overlapping} resource=default: 2 booking(s) in 1 overlapping pair(s)" in message
    assert f"company_id={pending_overlap} resource=default: 2 booking(s) in 1 overlapping pair(s)" in message
    assert f"company_id={broken_interval}: 1 active booking(s) with ends_at <= starts_at" in message
    assert REPORT_COMMAND in message
    for private in (secret_contact, "Maria Sigilosa", "nota privada"):
        assert private not in message
    assert "070_booking_capacity" not in _applied_versions()
    assert _booking_snapshot() == before
    assert not _resource_table_exists()

    report = build_capacity_report()
    assert {(pair["booking_id"], pair["conflicting_booking_id"]) for pair in report["overlapping_pairs"]} == {
        (first, second),
        (third, fourth),
    }
    assert [row["booking_id"] for row in report["invalid_intervals"]] == [empty]
    assert all("contact_id" not in pair and "title" not in pair for pair in report["overlapping_pairs"])

    # Resolução explícita do operador (cancelar um de cada par) libera o upgrade.
    for booking_id in (second, fourth, empty):
        _execute("UPDATE appointment_bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    db_module.init_db()
    assert "070_booking_capacity" in _applied_versions()
    assert build_capacity_report()["overlapping_pairs"] == []
