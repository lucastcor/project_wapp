from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.exceptions import TenantResourceNotFoundError
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()

VALID_BOOKING_OUTCOMES = {"attended", "no_show", "late_arrival"}
VALID_PERIODS = {"7d", "30d", "90d", "6m", "12m", "all"}


class AnalyticsError(Exception):
    pass


def _resolve_period(period: str) -> tuple[str | None, str | None]:
    now = datetime.now(UTC)
    if period == "all":
        return None, None
    if period == "7d":
        start = now - timedelta(days=7)
    elif period == "30d":
        start = now - timedelta(days=30)
    elif period == "90d":
        start = now - timedelta(days=90)
    elif period == "6m":
        start = now - timedelta(days=183)
    elif period == "12m":
        start = now - timedelta(days=365)
    else:
        start = now - timedelta(days=30)
    return start.isoformat(), now.isoformat()


def record_booking_outcome(
    booking_id: int,
    outcome: str,
    notes: str | None = None,
    *,
    company_id: int | None = None,
) -> dict[str, Any]:
    if outcome not in VALID_BOOKING_OUTCOMES:
        raise AnalyticsError(
            f"Invalid outcome '{outcome}'. Must be one of: {', '.join(sorted(VALID_BOOKING_OUTCOMES))}"
        )

    booking = repository.get_appointment_booking(booking_id, company_id=company_id)
    if not booking:
        raise TenantResourceNotFoundError(f"Booking {booking_id} not found.")

    booking_company_id = booking.get("company_id")
    repository.update_appointment_booking_outcome(
        booking_id,
        outcome=outcome,
        notes=notes,
        company_id=booking_company_id,
    )

    updated = repository.get_appointment_booking(booking_id, company_id=booking_company_id)
    return updated or {}


def record_cancellation_reason(
    booking_id: int,
    reason: str,
    *,
    company_id: int | None = None,
) -> dict[str, Any]:
    booking = repository.get_appointment_booking(booking_id, company_id=company_id)
    if not booking:
        raise TenantResourceNotFoundError(f"Booking {booking_id} not found.")

    booking_company_id = booking.get("company_id")
    repository.update_appointment_booking_cancellation_reason(
        booking_id,
        reason,
        company_id=booking_company_id,
    )

    updated = repository.get_appointment_booking(booking_id, company_id=booking_company_id)
    return updated or {}


def get_no_show_dashboard(
    *,
    company_id: int | None = None,
    period: str = "30d",
) -> dict[str, Any]:
    starts_after, starts_before = _resolve_period(period)

    bookings = repository.get_analytics_bookings_summary(
        company_id=company_id,
        starts_after=starts_after,
        starts_before=starts_before,
    )

    reminders = repository.get_analytics_reminders_summary(
        company_id=company_id,
        created_after=starts_after,
        created_before=starts_before,
    )

    queue = repository.get_analytics_queue_summary(
        company_id=company_id,
        created_after=starts_after,
        created_before=starts_before,
    )

    reminder_effectiveness = repository.get_analytics_reminder_effectiveness(
        company_id=company_id,
        starts_after=starts_after,
        starts_before=starts_before,
    )

    trend = repository.get_analytics_monthly_trend(
        company_id=company_id,
        months=_period_to_months(period),
    )

    by_outcome = bookings["by_outcome"]
    attended = by_outcome.get("attended", 0)
    no_show = by_outcome.get("no_show", 0)
    late = by_outcome.get("late_arrival", 0)
    resolved = attended + no_show + late
    pending_outcome = by_outcome.get("pending", 0) + by_outcome.get("None", 0)

    confirmed = bookings["by_status"].get("confirmed", 0)
    cancelled = bookings["by_status"].get("cancelled", 0)

    return {
        "period": period,
        "generated_at": datetime.now(UTC).isoformat(),
        "kpis": {
            "total_bookings": bookings["total"],
            "confirmed": confirmed,
            "cancelled": cancelled,
            "attended": attended,
            "no_show": no_show,
            "late_arrival": late,
            "pending_outcome": pending_outcome,
            "cancellation_rate": round(cancelled / max(bookings["total"], 1) * 100, 1),
            "no_show_rate": round(no_show / max(resolved, 1) * 100, 1),
            "attendance_rate": round(attended / max(resolved, 1) * 100, 1),
        },
        "funnel": {
            "bookings_created": bookings["total"],
            "still_confirmed": confirmed,
            "cancelled": cancelled,
            "outcome_recorded": resolved,
            "attended": attended,
            "no_show": no_show,
        },
        "reminders": {
            "total_sent": reminders["sent"],
            "total_pending": reminders["pending"],
            "total_cancelled": reminders["cancelled"],
            "effectiveness": reminder_effectiveness,
        },
        "queue_recovery": queue,
        "cancellation_reasons": bookings["cancellation_reasons"],
        "monthly_trend": trend,
    }


def _period_to_months(period: str) -> int:
    mapping = {
        "7d": 1,
        "30d": 2,
        "90d": 4,
        "6m": 7,
        "12m": 13,
        "all": 24,
    }
    return mapping.get(period, 2)
