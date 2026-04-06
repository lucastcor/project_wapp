import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from app.core.company_context import get_company_context
from app.core.config import settings
from app.services.reminder_service import schedule_booking_customer_reminders
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
DAY_SCHEDULE_KEYS = {
    0: ("monday", "segunda"),
    1: ("tuesday", "terca", "terça"),
    2: ("wednesday", "quarta"),
    3: ("thursday", "quinta"),
    4: ("friday", "sexta"),
    5: ("saturday", "sabado", "sábado"),
    6: ("sunday", "domingo"),
}
SUPPORTED_CALENDAR_PROVIDERS = {
    "mock": {
        "label": "Mock",
        "integration_mode": "local",
        "supports_live_sync": False,
        "notes": "Provider local para validacao de agenda e UX sem credenciais externas.",
    },
    "google_calendar": {
        "label": "Google Calendar",
        "integration_mode": "oauth_refresh_token",
        "supports_live_sync": True,
        "notes": "Usa a API oficial do Google Calendar para freebusy e criacao de eventos.",
    },
    "caldav": {
        "label": "CalDAV / Apple iCloud",
        "integration_mode": "basic_auth_or_app_password",
        "supports_live_sync": True,
        "notes": "Abordagem compativel com iCloud/Apple Calendar e outros servidores CalDAV.",
    },
}


class CalendarProviderError(Exception):
    pass


@dataclass
class BusyInterval:
    starts_at: datetime
    ends_at: datetime


def _get_timezone() -> ZoneInfo:
    return ZoneInfo(settings.calendar_timezone)


def _resolve_company_context(company_id: int | None) -> dict[str, Any]:
    if company_id is None:
        return get_company_context()

    get_company = getattr(repository, "get_company", None)
    company = get_company(company_id) if callable(get_company) else None
    company_slug = company.get("slug") if company else None
    return get_company_context(company_slug=company_slug)


def _get_company_timezone(
    *,
    company_id: int | None = None,
    company_context: dict[str, Any] | None = None,
) -> ZoneInfo:
    resolved_context = company_context or _resolve_company_context(company_id)
    timezone_name = (
        resolved_context.get("business_hours", {}).get("timezone")
        or settings.calendar_timezone
    )
    return ZoneInfo(str(timezone_name))


def _parse_clock(value: str) -> time:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _parse_workday_window(value: Any) -> tuple[time, time] | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"fechado", "closed", "off"}:
        return None

    match = re.fullmatch(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", normalized)
    if not match:
        return None

    starts_at = _parse_clock(match.group(1))
    ends_at = _parse_clock(match.group(2))
    if starts_at >= ends_at:
        return None
    return starts_at, ends_at


def _parse_iso_datetime(value: str, timezone_name: str | None = None) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo(timezone_name or settings.calendar_timezone))
    return parsed


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _to_provider_slot(slot_start: datetime, duration_minutes: int, provider: str) -> dict[str, Any]:
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    return {
        "provider": provider,
        "starts_at": slot_start.isoformat(),
        "ends_at": slot_end.isoformat(),
        "timezone": str(slot_start.tzinfo or settings.calendar_timezone),
        "duration_minutes": duration_minutes,
    }


def _intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def _resolve_workday_window(
    current_day: date,
    company_context: dict[str, Any],
) -> tuple[time, time] | None:
    business_hours = company_context.get("business_hours", {})
    weekday = current_day.weekday()

    configured_value = None
    for key in DAY_SCHEDULE_KEYS.get(weekday, ()):
        if key in business_hours:
            configured_value = business_hours.get(key)
            break

    if configured_value is None and weekday < 5:
        configured_value = business_hours.get("weekdays")

    window = _parse_workday_window(configured_value)
    if window:
        return window

    if configured_value is not None:
        return None

    return (
        _parse_clock(settings.calendar_default_workday_start),
        _parse_clock(settings.calendar_default_workday_end),
    )


def _resolve_slot_increment_minutes(company_context: dict[str, Any]) -> int:
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("slot_increment_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured
    return max(5, settings.calendar_slot_increment_minutes)


def _default_provider_config(provider: str) -> dict[str, Any]:
    if provider == "google_calendar":
        return {
            "calendar_id": settings.google_calendar_id,
            "client_id": settings.google_calendar_client_id,
            "client_secret_present": bool(settings.google_calendar_client_secret),
            "refresh_token_present": bool(settings.google_calendar_refresh_token),
        }
    if provider == "caldav":
        return {
            "url": settings.caldav_url,
            "username": settings.caldav_username,
            "password_present": bool(settings.caldav_password),
            "calendar_name": settings.caldav_calendar_name,
        }
    return {}


def _resolve_calendar_integration(company_id: int | None = None) -> dict[str, Any]:
    integration = repository.get_active_calendar_integration(company_id=company_id)
    if integration:
        config = integration.get("config") or {}
        return {
            "integration_id": integration.get("id"),
            "company_id": integration.get("company_id"),
            "provider": integration.get("provider"),
            "external_calendar_id": integration.get("external_calendar_id"),
            "config": config,
            "active": bool(integration.get("active")),
            "source": "database",
        }

    provider = settings.calendar_provider.strip().lower() or "mock"
    return {
        "integration_id": None,
        "company_id": company_id,
        "provider": provider,
        "external_calendar_id": None,
        "config": _default_provider_config(provider),
        "active": True,
        "source": "environment",
    }


def _get_local_busy_intervals(
    company_id: int | None,
    window_start: datetime,
    window_end: datetime,
) -> list[BusyInterval]:
    bookings = repository.get_appointment_bookings_between(
        company_id=company_id,
        starts_at=_to_utc_iso(window_start),
        ends_at=_to_utc_iso(window_end),
    )
    intervals: list[BusyInterval] = []
    for booking in bookings:
        intervals.append(
            BusyInterval(
                starts_at=_parse_iso_datetime(str(booking["starts_at"]), str(booking.get("timezone") or settings.calendar_timezone)),
                ends_at=_parse_iso_datetime(str(booking["ends_at"]), str(booking.get("timezone") or settings.calendar_timezone)),
            )
        )
    return intervals


class BaseCalendarProvider:
    provider_name = "base"

    def __init__(self, integration: dict[str, Any]) -> None:
        self.integration = integration

    def readiness(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "ready": True,
            "details": "Provider pronto.",
        }

    def list_busy_intervals(
        self,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BusyInterval]:
        return []

    def create_external_event(
        self,
        *,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        return None


class MockCalendarProvider(BaseCalendarProvider):
    provider_name = "mock"


class GoogleCalendarProvider(BaseCalendarProvider):
    provider_name = "google_calendar"

    def _get_google_service(self):
        try:
            from google.oauth2.credentials import Credentials  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError as exc:
            raise CalendarProviderError(
                "Google Calendar requer google-auth e google-api-python-client instalados."
            ) from exc

        config = self.integration.get("config") or {}
        calendar_id = config.get("calendar_id") or self.integration.get("external_calendar_id")
        client_id = config.get("client_id") or settings.google_calendar_client_id
        client_secret = config.get("client_secret") or settings.google_calendar_client_secret
        refresh_token = config.get("refresh_token") or settings.google_calendar_refresh_token
        if not all([calendar_id, client_id, client_secret, refresh_token]):
            raise CalendarProviderError("Google Calendar não está configurado com calendar_id, client_id, client_secret e refresh_token.")

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[CALENDAR_SCOPE],
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return service, str(calendar_id)

    def readiness(self) -> dict[str, Any]:
        config = self.integration.get("config") or {}
        ready = bool(
            (config.get("calendar_id") or self.integration.get("external_calendar_id") or settings.google_calendar_id)
            and (config.get("client_id") or settings.google_calendar_client_id)
            and (config.get("client_secret") or settings.google_calendar_client_secret)
            and (config.get("refresh_token") or settings.google_calendar_refresh_token)
        )
        return {
            "provider": self.provider_name,
            "ready": ready,
            "details": "Credenciais Google Calendar encontradas." if ready else "Faltam credenciais do Google Calendar.",
        }

    def list_busy_intervals(self, window_start: datetime, window_end: datetime) -> list[BusyInterval]:
        service, calendar_id = self._get_google_service()
        response = (
            service.freebusy()
            .query(
                body={
                    "timeMin": window_start.astimezone(UTC).isoformat(),
                    "timeMax": window_end.astimezone(UTC).isoformat(),
                    "timeZone": str(window_start.tzinfo or _get_timezone()),
                    "items": [{"id": calendar_id}],
                }
            )
            .execute()
        )

        intervals: list[BusyInterval] = []
        for item in response.get("calendars", {}).get(calendar_id, {}).get("busy", []):
            intervals.append(
                BusyInterval(
                    starts_at=_parse_iso_datetime(item["start"]),
                    ends_at=_parse_iso_datetime(item["end"]),
                )
            )
        return intervals

    def create_external_event(
        self,
        *,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        service, calendar_id = self._get_google_service()
        response = (
            service.events()
            .insert(
                calendarId=calendar_id,
                body={
                    "summary": title,
                    "description": description or "",
                    "start": {
                        "dateTime": starts_at.isoformat(),
                        "timeZone": timezone_name,
                    },
                    "end": {
                        "dateTime": ends_at.isoformat(),
                        "timeZone": timezone_name,
                    },
                },
            )
            .execute()
        )
        return str(response.get("id")) if response.get("id") else None


class CalDAVCalendarProvider(BaseCalendarProvider):
    provider_name = "caldav"

    def _get_calendar(self):
        try:
            import caldav  # type: ignore
        except ImportError as exc:
            raise CalendarProviderError("CalDAV requer a biblioteca caldav instalada.") from exc

        config = self.integration.get("config") or {}
        url = config.get("url") or settings.caldav_url
        username = config.get("username") or settings.caldav_username
        password = config.get("password") or settings.caldav_password
        calendar_name = config.get("calendar_name") or settings.caldav_calendar_name
        if not all([url, username, password]):
            raise CalendarProviderError("CalDAV não está configurado com url, username e password.")

        client = caldav.DAVClient(url=str(url), username=str(username), password=str(password))
        principal = client.principal()
        calendars = principal.calendars()
        if calendar_name:
            for calendar in calendars:
                if getattr(calendar, "name", "") == calendar_name:
                    return calendar
        if not calendars:
            raise CalendarProviderError("Nenhum calendário CalDAV disponível.")
        return calendars[0]

    def readiness(self) -> dict[str, Any]:
        config = self.integration.get("config") or {}
        ready = bool(
            (config.get("url") or settings.caldav_url)
            and (config.get("username") or settings.caldav_username)
            and (config.get("password") or settings.caldav_password)
        )
        return {
            "provider": self.provider_name,
            "ready": ready,
            "details": "Credenciais CalDAV encontradas." if ready else "Faltam credenciais do servidor CalDAV.",
        }

    def list_busy_intervals(self, window_start: datetime, window_end: datetime) -> list[BusyInterval]:
        calendar = self._get_calendar()
        events = calendar.date_search(start=window_start, end=window_end)
        intervals: list[BusyInterval] = []
        for event in events:
            vobject_instance = getattr(event, "vobject_instance", None)
            if not vobject_instance or not hasattr(vobject_instance, "vevent"):
                continue
            vevent = vobject_instance.vevent
            intervals.append(
                BusyInterval(
                    starts_at=_parse_iso_datetime(str(vevent.dtstart.value.isoformat())),
                    ends_at=_parse_iso_datetime(str(vevent.dtend.value.isoformat())),
                )
            )
        return intervals

    def create_external_event(
        self,
        *,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        calendar = self._get_calendar()
        uid = str(uuid.uuid4())
        ical = "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Project WAPP//Calendar//PT-BR",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{starts_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{ends_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:{title}",
                f"DESCRIPTION:{(description or '').replace(chr(10), '\\n')}",
                f"X-WR-TIMEZONE:{timezone_name}",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )
        event = calendar.save_event(ical)
        return str(getattr(event, "url", uid))


def _get_provider(integration: dict[str, Any]) -> BaseCalendarProvider:
    provider_name = str(integration.get("provider") or "mock").strip().lower()
    if provider_name == "google_calendar":
        return GoogleCalendarProvider(integration)
    if provider_name == "caldav":
        return CalDAVCalendarProvider(integration)
    return MockCalendarProvider(integration)


def list_supported_calendar_providers() -> list[dict[str, Any]]:
    return [
        {
            "provider": provider,
            **metadata,
        }
        for provider, metadata in SUPPORTED_CALENDAR_PROVIDERS.items()
    ]


def get_calendar_profile(company_id: int | None = None) -> dict[str, Any]:
    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    return {
        "integration": integration,
        "supported_providers": list_supported_calendar_providers(),
        "provider_readiness": readiness,
    }


def query_calendar_availability(
    *,
    company_id: int | None,
    start_date: str,
    end_date: str,
    duration_minutes: int,
    limit: int = 12,
) -> dict[str, Any]:
    if duration_minutes <= 0:
        raise CalendarProviderError("duration_minutes must be greater than zero.")

    company_context = _resolve_company_context(company_id)
    timezone_obj = _get_company_timezone(company_context=company_context)
    start_day = date.fromisoformat(start_date)
    end_day = date.fromisoformat(end_date)
    if end_day < start_day:
        raise CalendarProviderError("end_date must be greater than or equal to start_date.")

    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    if not readiness["ready"] and integration["provider"] != "mock":
        raise CalendarProviderError(str(readiness["details"]))

    window_start = datetime.combine(start_day, time.min, tzinfo=timezone_obj)
    window_end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone_obj)
    busy_intervals = provider.list_busy_intervals(window_start, window_end) + _get_local_busy_intervals(
        company_id=company_id,
        window_start=window_start,
        window_end=window_end,
    )

    increment = _resolve_slot_increment_minutes(company_context)

    slots: list[dict[str, Any]] = []
    current_day = start_day
    while current_day <= end_day and len(slots) < limit:
        workday_window = _resolve_workday_window(current_day, company_context)
        if not workday_window:
            current_day += timedelta(days=1)
            continue

        work_start, work_end = workday_window
        slot_cursor = datetime.combine(current_day, work_start, tzinfo=timezone_obj)
        workday_end = datetime.combine(current_day, work_end, tzinfo=timezone_obj)

        while slot_cursor + timedelta(minutes=duration_minutes) <= workday_end and len(slots) < limit:
            slot_end = slot_cursor + timedelta(minutes=duration_minutes)
            has_conflict = any(
                _intervals_overlap(slot_cursor, slot_end, interval.starts_at, interval.ends_at)
                for interval in busy_intervals
            )
            if not has_conflict:
                slots.append(_to_provider_slot(slot_cursor, duration_minutes, str(integration["provider"])))
            slot_cursor += timedelta(minutes=increment)

        current_day += timedelta(days=1)

    return {
        "company_id": company_id,
        "provider": integration["provider"],
        "integration_source": integration["source"],
        "slots": slots,
        "provider_readiness": readiness,
    }


def create_calendar_booking(
    *,
    company_id: int | None,
    contact_id: str | None,
    title: str,
    starts_at: str,
    ends_at: str,
    timezone_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    company_context = _resolve_company_context(company_id)
    timezone_value = timezone_name or str(_get_company_timezone(company_context=company_context))
    starts_at_dt = _parse_iso_datetime(starts_at, timezone_value)
    ends_at_dt = _parse_iso_datetime(ends_at, timezone_value)
    if ends_at_dt <= starts_at_dt:
        raise CalendarProviderError("ends_at must be greater than starts_at.")

    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    if not readiness["ready"] and integration["provider"] != "mock":
        raise CalendarProviderError(str(readiness["details"]))

    local_conflicts = _get_local_busy_intervals(company_id, starts_at_dt, ends_at_dt)
    if any(_intervals_overlap(starts_at_dt, ends_at_dt, item.starts_at, item.ends_at) for item in local_conflicts):
        raise CalendarProviderError("O horário solicitado conflita com um agendamento já existente.")

    external_event_id = provider.create_external_event(
        title=title,
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
        timezone_name=timezone_value,
        description=description,
    )
    booking_id = repository.create_appointment_booking(
        company_id=company_id,
        contact_id=contact_id,
        integration_id=integration.get("integration_id"),
        provider=str(integration["provider"]),
        external_event_id=external_event_id,
        title=title,
        description=description,
        starts_at=_to_utc_iso(starts_at_dt),
        ends_at=_to_utc_iso(ends_at_dt),
        timezone=timezone_value,
        status="confirmed",
    )
    booking = next(
        (
            item
            for item in repository.get_appointment_bookings(company_id=company_id)
            if int(item["id"]) == booking_id
        ),
        None,
    )
    if not booking:
        raise CalendarProviderError("Booking não encontrado após criação.")

    scheduled_reminders = schedule_booking_customer_reminders(booking)

    return {
        **booking,
        "provider_readiness": readiness,
        "scheduled_reminders": scheduled_reminders,
    }


def create_calendar_integration(
    *,
    provider: str,
    company_id: int | None = None,
    external_calendar_id: str | None = None,
    config: dict[str, Any] | None = None,
    active: bool = True,
) -> dict[str, Any]:
    normalized_provider = provider.strip().lower()
    if normalized_provider not in SUPPORTED_CALENDAR_PROVIDERS:
        raise CalendarProviderError(f"Unsupported calendar provider: {provider}")

    integration_id = repository.create_calendar_integration(
        company_id=company_id,
        provider=normalized_provider,
        external_calendar_id=external_calendar_id,
        config=config,
        active=active,
    )
    integration = repository.get_calendar_integration(integration_id)
    if not integration:
        raise CalendarProviderError("Calendar integration not found after creation.")
    return integration


def get_calendar_integrations(company_id: int | None = None) -> list[dict[str, Any]]:
    return repository.get_calendar_integrations(company_id=company_id)


def get_calendar_bookings(company_id: int | None = None) -> list[dict[str, Any]]:
    return repository.get_appointment_bookings(company_id=company_id)
