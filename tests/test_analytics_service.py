from app.services import analytics_service
from app.services.analytics_service import (
    AnalyticsError,
    get_no_show_dashboard,
    record_booking_outcome,
    record_cancellation_reason,
)


class FakeRepository:
    def __init__(self):
        self.bookings = {}
        self.outcomes = {}
        self.cancellation_reasons = {}
        self._next_id = 1

    def _add_booking(self, **kwargs):
        booking_id = self._next_id
        self._next_id += 1
        self.bookings[booking_id] = {
            "id": booking_id,
            "company_id": kwargs.get("company_id", 1),
            "contact_id": kwargs.get("contact_id", "c1"),
            "title": kwargs.get("title", "Consulta"),
            "starts_at": kwargs.get("starts_at", "2026-04-10T10:00:00+00:00"),
            "ends_at": kwargs.get("ends_at", "2026-04-10T11:00:00+00:00"),
            "timezone": "America/Sao_Paulo",
            "status": kwargs.get("status", "confirmed"),
            "booking_outcome": kwargs.get("booking_outcome", None),
            "booking_outcome_at": None,
            "booking_outcome_notes": None,
            "cancellation_reason": kwargs.get("cancellation_reason", None),
            "created_at": "2026-04-09T08:00:00+00:00",
            "updated_at": "2026-04-09T08:00:00+00:00",
        }
        return booking_id

    def get_appointment_booking(self, booking_id, *, company_id=None):
        booking = self.bookings.get(booking_id)
        if booking is None:
            return None
        if company_id is not None and booking.get("company_id") != company_id:
            return None
        return booking

    def update_appointment_booking_outcome(
        self,
        booking_id,
        *,
        outcome,
        notes=None,
        company_id=None,
    ):
        if booking_id in self.bookings:
            booking = self.bookings[booking_id]
            if company_id is not None and booking.get("company_id") != company_id:
                return
            booking["booking_outcome"] = outcome
            booking["booking_outcome_notes"] = notes

    def update_appointment_booking_cancellation_reason(
        self,
        booking_id,
        reason,
        *,
        company_id=None,
    ):
        if booking_id in self.bookings:
            booking = self.bookings[booking_id]
            if company_id is not None and booking.get("company_id") != company_id:
                return
            booking["cancellation_reason"] = reason

    def get_analytics_bookings_summary(self, *, company_id=None, starts_after=None, starts_before=None):
        return {
            "total": 4,
            "by_status": {"confirmed": 3, "cancelled": 1},
            "by_outcome": {"attended": 2, "no_show": 1, "pending": 1},
            "cancellation_reasons": {"conflito de horario": 1},
        }

    def get_analytics_reminders_summary(self, *, company_id=None, created_after=None, created_before=None):
        return {
            "total": 5,
            "by_status": {"sent": 3, "pending": 1, "cancelled": 1},
            "sent": 3,
            "pending": 1,
            "cancelled": 1,
        }

    def get_analytics_queue_summary(self, *, company_id=None, created_after=None, created_before=None):
        return {
            "entries": {"total": 4, "by_status": {"active": 2, "fulfilled": 2}},
            "offers": {
                "total": 3,
                "by_status": {"accepted": 2, "expired": 1},
                "accepted": 2,
                "expired": 1,
                "declined": 0,
            },
            "recovery_rate": 66.7,
        }

    def get_analytics_monthly_trend(self, *, company_id=None, months=6):
        return [
            {
                "month": "2026-03",
                "total": 10,
                "confirmed": 6,
                "cancelled": 2,
                "attended": 5,
                "no_show": 3,
                "no_show_rate": 37.5,
                "attendance_rate": 62.5,
            },
            {
                "month": "2026-04",
                "total": 8,
                "confirmed": 5,
                "cancelled": 1,
                "attended": 6,
                "no_show": 1,
                "no_show_rate": 14.3,
                "attendance_rate": 85.7,
            },
        ]

    def get_analytics_reminder_effectiveness(self, *, company_id=None, starts_after=None, starts_before=None):
        return {
            "total_bookings_with_outcome": 8,
            "with_reminder": {
                "total": 5,
                "attended": 4,
                "no_show": 1,
                "no_show_rate": 20.0,
                "attendance_rate": 80.0,
            },
            "without_reminder": {
                "total": 3,
                "attended": 1,
                "no_show": 2,
                "no_show_rate": 66.7,
                "attendance_rate": 33.3,
            },
            "reminder_impact": {
                "no_show_reduction_pp": 46.7,
            },
        }


def _patch_repo(monkeypatch, repo=None):
    fake = repo or FakeRepository()
    monkeypatch.setattr(analytics_service, "repository", fake)
    return fake


def test_record_booking_outcome_attended(monkeypatch):
    repo = _patch_repo(monkeypatch)
    bid = repo._add_booking()
    result = record_booking_outcome(bid, "attended", notes="Chegou no horario")
    assert result["booking_outcome"] == "attended"
    assert result["booking_outcome_notes"] == "Chegou no horario"


def test_record_booking_outcome_no_show(monkeypatch):
    repo = _patch_repo(monkeypatch)
    bid = repo._add_booking()
    result = record_booking_outcome(bid, "no_show")
    assert result["booking_outcome"] == "no_show"


def test_record_booking_outcome_late_arrival(monkeypatch):
    repo = _patch_repo(monkeypatch)
    bid = repo._add_booking()
    result = record_booking_outcome(bid, "late_arrival")
    assert result["booking_outcome"] == "late_arrival"


def test_record_booking_outcome_invalid_raises(monkeypatch):
    repo = _patch_repo(monkeypatch)
    bid = repo._add_booking()
    try:
        record_booking_outcome(bid, "invalid_outcome")
        assert False, "Should have raised AnalyticsError"
    except AnalyticsError as exc:
        assert "Invalid outcome" in str(exc)


def test_record_booking_outcome_not_found_raises(monkeypatch):
    _patch_repo(monkeypatch)
    try:
        record_booking_outcome(999, "attended")
        assert False, "Should have raised AnalyticsError"
    except AnalyticsError as exc:
        assert "not found" in str(exc)


def test_record_cancellation_reason(monkeypatch):
    repo = _patch_repo(monkeypatch)
    bid = repo._add_booking(status="cancelled")
    result = record_cancellation_reason(bid, "conflito de horario")
    assert result["cancellation_reason"] == "conflito de horario"


def test_record_cancellation_reason_not_found(monkeypatch):
    _patch_repo(monkeypatch)
    try:
        record_cancellation_reason(999, "motivo")
        assert False, "Should have raised AnalyticsError"
    except AnalyticsError as exc:
        assert "not found" in str(exc)


def test_get_no_show_dashboard_returns_all_sections(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(company_id=1, period="30d")

    assert dashboard["period"] == "30d"
    assert "generated_at" in dashboard
    assert "kpis" in dashboard
    assert "funnel" in dashboard
    assert "reminders" in dashboard
    assert "queue_recovery" in dashboard
    assert "cancellation_reasons" in dashboard
    assert "monthly_trend" in dashboard


def test_get_no_show_dashboard_kpis(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(period="30d")
    kpis = dashboard["kpis"]

    assert kpis["total_bookings"] == 4
    assert kpis["attended"] == 2
    assert kpis["no_show"] == 1
    assert kpis["no_show_rate"] == 33.3
    assert kpis["attendance_rate"] == 66.7


def test_get_no_show_dashboard_funnel(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(period="30d")
    funnel = dashboard["funnel"]

    assert funnel["bookings_created"] == 4
    assert funnel["attended"] == 2
    assert funnel["no_show"] == 1


def test_get_no_show_dashboard_reminders(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(period="30d")
    reminders = dashboard["reminders"]

    assert reminders["total_sent"] == 3
    assert "effectiveness" in reminders
    assert reminders["effectiveness"]["reminder_impact"]["no_show_reduction_pp"] == 46.7


def test_get_no_show_dashboard_queue_recovery(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(period="30d")
    queue = dashboard["queue_recovery"]

    assert queue["recovery_rate"] == 66.7
    assert queue["offers"]["accepted"] == 2


def test_get_no_show_dashboard_trend(monkeypatch):
    _patch_repo(monkeypatch)

    dashboard = get_no_show_dashboard(period="6m")
    trend = dashboard["monthly_trend"]

    assert len(trend) == 2
    assert trend[0]["month"] == "2026-03"
    assert trend[1]["no_show_rate"] == 14.3


def test_get_no_show_dashboard_all_periods(monkeypatch):
    _patch_repo(monkeypatch)

    for period in ("7d", "30d", "90d", "6m", "12m", "all"):
        dashboard = get_no_show_dashboard(period=period)
        assert dashboard["period"] == period


def test_resolve_period():
    from app.services.analytics_service import _resolve_period

    starts, ends = _resolve_period("all")
    assert starts is None
    assert ends is None

    starts, ends = _resolve_period("30d")
    assert starts is not None
    assert ends is not None

    starts, ends = _resolve_period("7d")
    assert starts is not None
