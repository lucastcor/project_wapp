import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.company_context import get_company_context
from app.core.config import is_missing_or_placeholder, settings
from app.services.automation_trigger_service import emit_appointment_booked
from app.services.billing_service import (
    BillingLimitError,
    refund_usage_reservation,
    require_usage_capacity,
)
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
WEEKDAY_ALIAS_MAP = {
    alias: aliases[0]
    for aliases in DAY_SCHEDULE_KEYS.values()
    for alias in aliases
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


def _has_real_provider_value(value: Any) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and not is_missing_or_placeholder(normalized)


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


def _normalize_lookup_key(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    replacements = str.maketrans({
        "á": "a",
        "à": "a",
        "ã": "a",
        "â": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ç": "c",
    })
    normalized = normalized.translate(replacements)
    return re.sub(r"\s+", " ", normalized)


def _normalize_weekday_key(value: Any) -> str | None:
    normalized = _normalize_lookup_key(value)
    if not normalized:
        return None
    return WEEKDAY_ALIAS_MAP.get(normalized, normalized)


def _normalize_string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        candidates = re.split(r"[,\n;]+", values)
    elif isinstance(values, list):
        candidates = values
    else:
        return []
    normalized: list[str] = []
    for item in candidates:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _parse_workday_windows(value: Any) -> list[tuple[time, time]]:
    if isinstance(value, list):
        nested_windows: list[tuple[time, time]] = []
        for item in value:
            nested_windows.extend(_parse_workday_windows(item))
        return nested_windows

    normalized = _normalize_lookup_key(value)
    if not normalized:
        return []
    if normalized in {"fechado", "closed", "off"}:
        return []

    windows: list[tuple[time, time]] = []
    for chunk in re.split(r"[;,]+", normalized):
        candidate = chunk.strip()
        if not candidate:
            continue
        match = re.fullmatch(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", candidate)
        if not match:
            continue
        starts_at = _parse_clock(match.group(1))
        ends_at = _parse_clock(match.group(2))
        if starts_at >= ends_at:
            continue
        windows.append((starts_at, ends_at))
    return windows


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


def _resolve_workday_windows(
    current_day: date,
    company_context: dict[str, Any],
) -> list[tuple[time, time]]:
    business_hours = company_context.get("business_hours", {})
    weekday = current_day.weekday()

    configured_value = None
    for key in DAY_SCHEDULE_KEYS.get(weekday, ()):
        if key in business_hours:
            configured_value = business_hours.get(key)
            break

    if configured_value is None and weekday < 5:
        configured_value = business_hours.get("weekdays")

    windows = _parse_workday_windows(configured_value)
    if windows:
        return windows

    if configured_value is not None:
        return []

    return [
        (
            _parse_clock(settings.calendar_default_workday_start),
            _parse_clock(settings.calendar_default_workday_end),
        )
    ]


def _profile_enabled(profile: dict[str, Any]) -> bool:
    return bool(profile.get("enabled", True))


def _list_scheduling_profiles(company_context: dict[str, Any]) -> list[dict[str, Any]]:
    scheduling_policy = company_context.get("scheduling_policy", {})
    profiles = scheduling_policy.get("profiles")
    if not isinstance(profiles, list):
        return []
    return [profile for profile in profiles if isinstance(profile, dict) and _profile_enabled(profile)]


def resolve_scheduling_profile(
    *,
    company_context: dict[str, Any],
    requested_profile_key: str | None = None,
    service_name: str | None = None,
    customer_profile: str | None = None,
    request_context: str | None = None,
) -> dict[str, Any] | None:
    profiles = _list_scheduling_profiles(company_context)
    if not profiles:
        return None

    normalized_requested_key = _normalize_lookup_key(requested_profile_key)
    if normalized_requested_key:
        for profile in profiles:
            if _normalize_lookup_key(profile.get("key")) == normalized_requested_key:
                return profile
        raise CalendarProviderError(
            f"scheduling_profile_key '{requested_profile_key}' is not configured for this company."
        )

    default_profile_key = _normalize_lookup_key(
        company_context.get("scheduling_policy", {}).get("default_profile_key")
    )
    normalized_service_name = _normalize_lookup_key(service_name)
    normalized_customer_profile = _normalize_lookup_key(customer_profile)
    normalized_request_context = _normalize_lookup_key(request_context)

    ranked_candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, profile in enumerate(profiles):
        score = 0
        profile_customer_profiles = {
            _normalize_lookup_key(item)
            for item in _normalize_string_list(profile.get("customer_profiles"))
        }
        if normalized_customer_profile and normalized_customer_profile in profile_customer_profiles:
            score += 100

        profile_service_names = {
            _normalize_lookup_key(item)
            for item in _normalize_string_list(profile.get("service_names"))
        }
        if normalized_service_name and normalized_service_name in profile_service_names:
            score += 50

        profile_keywords = {
            _normalize_lookup_key(item)
            for item in _normalize_string_list(profile.get("match_keywords"))
        }
        if normalized_request_context and any(keyword and keyword in normalized_request_context for keyword in profile_keywords):
            score += 25

        if default_profile_key and _normalize_lookup_key(profile.get("key")) == default_profile_key:
            score += 5

        if score > 0:
            ranked_candidates.append((score, -index, profile))

    if ranked_candidates:
        ranked_candidates.sort(reverse=True)
        return ranked_candidates[0][2]

    if default_profile_key:
        for profile in profiles:
            if _normalize_lookup_key(profile.get("key")) == default_profile_key:
                return profile

    if len(profiles) == 1:
        return profiles[0]

    return None


def _resolve_profile_daily_windows(
    *,
    current_day: date,
    scheduling_profile: dict[str, Any],
    company_context: dict[str, Any],
) -> list[tuple[time, time]]:
    weekday = current_day.weekday()
    normalized_allowed_weekdays = {
        _normalize_weekday_key(item)
        for item in _normalize_string_list(scheduling_profile.get("allowed_weekdays"))
        if _normalize_weekday_key(item)
    }
    current_weekday_key = DAY_SCHEDULE_KEYS[weekday][0]
    if normalized_allowed_weekdays and current_weekday_key not in normalized_allowed_weekdays:
        return []

    day_windows = scheduling_profile.get("day_windows")
    if isinstance(day_windows, dict):
        configured_values: list[Any] = [
            day_windows.get(key)
            for key in DAY_SCHEDULE_KEYS.get(weekday, ())
            if key in day_windows
        ]
        if weekday < 5:
            configured_values.extend(
                day_windows.get(key)
                for key in ("weekdays", "dias_uteis", "dias úteis")
                if key in day_windows
            )
        configured_values.extend(
            day_windows.get(key)
            for key in ("all", "todos")
            if key in day_windows
        )
        windows: list[tuple[time, time]] = []
        for configured_value in configured_values:
            windows.extend(_parse_workday_windows(configured_value))
        if windows:
            return windows
        if configured_values:
            return []

    allowed_windows = scheduling_profile.get("allowed_windows")
    windows = _parse_workday_windows(allowed_windows)
    if windows:
        return windows
    if allowed_windows is not None:
        return []

    return _resolve_workday_windows(current_day, company_context)


def _resolve_slot_increment_minutes(
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None = None,
) -> int:
    configured = (scheduling_profile or {}).get("slot_increment_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("slot_increment_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured
    return max(5, settings.calendar_slot_increment_minutes)


def _resolve_minimum_notice_minutes(
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None = None,
) -> int:
    configured = (scheduling_profile or {}).get("minimum_notice_minutes")
    if isinstance(configured, int) and configured >= 0:
        return configured
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("minimum_notice_minutes")
    if isinstance(configured, int) and configured >= 0:
        return configured
    return 0


def _resolve_default_slot_duration_minutes(
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None = None,
) -> int:
    configured = (scheduling_profile or {}).get("default_duration_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured
    scheduling_policy = company_context.get("scheduling_policy", {})
    configured = scheduling_policy.get("default_slot_duration_minutes")
    if isinstance(configured, int) and configured > 0:
        return configured
    return 60


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
    ignore_booking_ids: set[int] | None = None,
) -> list[BusyInterval]:
    ignored_booking_ids = {int(item) for item in (ignore_booking_ids or set())}
    bookings = repository.get_appointment_bookings_between(
        company_id=company_id,
        starts_at=_to_utc_iso(window_start),
        ends_at=_to_utc_iso(window_end),
    )
    intervals: list[BusyInterval] = []
    for booking in bookings:
        if int(booking.get("id") or 0) in ignored_booking_ids:
            continue
        intervals.append(
            BusyInterval(
                starts_at=_parse_iso_datetime(str(booking["starts_at"]), str(booking.get("timezone") or settings.calendar_timezone)),
                ends_at=_parse_iso_datetime(str(booking["ends_at"]), str(booking.get("timezone") or settings.calendar_timezone)),
            )
        )
    return intervals


def _slot_respects_scheduling_policy(
    *,
    starts_at: datetime,
    ends_at: datetime,
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None,
) -> bool:
    timezone_obj = _get_company_timezone(company_context=company_context)
    localized_start = starts_at.astimezone(timezone_obj)
    localized_end = ends_at.astimezone(timezone_obj)
    if localized_end.date() != localized_start.date():
        return False

    daily_windows = (
        _resolve_profile_daily_windows(
            current_day=localized_start.date(),
            scheduling_profile=scheduling_profile,
            company_context=company_context,
        )
        if scheduling_profile
        else _resolve_workday_windows(localized_start.date(), company_context)
    )

    if not daily_windows:
        return False

    for work_start, work_end in daily_windows:
        window_start = datetime.combine(localized_start.date(), work_start, tzinfo=timezone_obj)
        window_end = datetime.combine(localized_start.date(), work_end, tzinfo=timezone_obj)
        if localized_start >= window_start and localized_end <= window_end:
            return True
    return False


def _validate_booking_window(
    *,
    starts_at: datetime,
    ends_at: datetime,
    company_context: dict[str, Any],
    scheduling_profile: dict[str, Any] | None,
) -> None:
    if not _slot_respects_scheduling_policy(
        starts_at=starts_at,
        ends_at=ends_at,
        company_context=company_context,
        scheduling_profile=scheduling_profile,
    ):
        if scheduling_profile:
            profile_label = scheduling_profile.get("label") or scheduling_profile.get("key")
            raise CalendarProviderError(
                f"O horário solicitado está fora da janela permitida para o perfil de agendamento '{profile_label}'."
            )
        raise CalendarProviderError("O horário solicitado está fora da janela disponível da empresa.")

    minimum_notice_minutes = _resolve_minimum_notice_minutes(company_context, scheduling_profile)
    if minimum_notice_minutes > 0:
        current_time = datetime.now(starts_at.tzinfo or UTC)
        if starts_at < current_time + timedelta(minutes=minimum_notice_minutes):
            raise CalendarProviderError(
                f"O horário solicitado precisa respeitar antecedência mínima de {minimum_notice_minutes} minuto(s)."
            )


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

    def cancel_external_event(
        self,
        *,
        external_event_id: str,
    ) -> bool:
        return False

    def update_external_event(
        self,
        *,
        external_event_id: str | None,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        if external_event_id:
            return external_event_id
        return self.create_external_event(
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            timezone_name=timezone_name,
            description=description,
        )


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

            token_uri="https://oauth2.googleapis.com/token",  # noqa: S106
            client_id=client_id,
            client_secret=client_secret,
            scopes=[CALENDAR_SCOPE],
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return service, str(calendar_id)

    def readiness(self) -> dict[str, Any]:
        config = self.integration.get("config") or {}
        calendar_id = config.get("calendar_id") or self.integration.get("external_calendar_id") or settings.google_calendar_id
        client_id = config.get("client_id") or settings.google_calendar_client_id
        client_secret = config.get("client_secret") or settings.google_calendar_client_secret
        refresh_token = config.get("refresh_token") or settings.google_calendar_refresh_token
        ready = bool(
            _has_real_provider_value(calendar_id)
            and _has_real_provider_value(client_id)
            and _has_real_provider_value(client_secret)
            and _has_real_provider_value(refresh_token)
        )
        return {
            "provider": self.provider_name,
            "ready": ready,
            "details": (
                "Credenciais Google Calendar encontradas."
                if ready
                else "Faltam credenciais reais do Google Calendar."
            ),
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

        intervals: list[BusyInterval] = [
            BusyInterval(
                starts_at=_parse_iso_datetime(item["start"]),
                ends_at=_parse_iso_datetime(item["end"]),
            )
            for item in response.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        ]
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

    def cancel_external_event(
        self,
        *,
        external_event_id: str,
    ) -> bool:
        service, calendar_id = self._get_google_service()
        service.events().delete(calendarId=calendar_id, eventId=external_event_id).execute()
        return True

    def update_external_event(
        self,
        *,
        external_event_id: str | None,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        if not external_event_id:
            return self.create_external_event(
                title=title,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone_name=timezone_name,
                description=description,
            )

        service, calendar_id = self._get_google_service()
        response = (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=external_event_id,
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
        return str(response.get("id") or external_event_id)


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
            _has_real_provider_value(config.get("url") or settings.caldav_url)
            and _has_real_provider_value(config.get("username") or settings.caldav_username)
            and _has_real_provider_value(config.get("password") or settings.caldav_password)
        )
        return {
            "provider": self.provider_name,
            "ready": ready,
            "details": (
                "Credenciais CalDAV encontradas."
                if ready
                else "Faltam credenciais reais do servidor CalDAV."
            ),
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

    def _get_event_by_reference(self, calendar: Any, external_event_id: str):
        normalized_reference = str(external_event_id or "").strip()
        if not normalized_reference:
            raise CalendarProviderError("CalDAV external_event_id is required.")

        if normalized_reference.startswith("http") and hasattr(calendar, "event_by_url"):
            event = calendar.event_by_url(normalized_reference)
            if event:
                return event

        if hasattr(calendar, "event"):
            try:
                event = calendar.event(normalized_reference)
                if event:
                    return event
            except Exception:
                pass

        if hasattr(calendar, "object_by_uid"):
            try:
                event = calendar.object_by_uid(normalized_reference)
                if event:
                    return event
            except Exception:
                pass

        raise CalendarProviderError("CalDAV não conseguiu localizar o evento externo para sincronização.")

    def cancel_external_event(
        self,
        *,
        external_event_id: str,
    ) -> bool:
        calendar = self._get_calendar()
        event = self._get_event_by_reference(calendar, external_event_id)
        event.delete()
        return True

    def update_external_event(
        self,
        *,
        external_event_id: str | None,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone_name: str,
        description: str | None = None,
    ) -> str | None:
        if not external_event_id:
            return self.create_external_event(
                title=title,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone_name=timezone_name,
                description=description,
            )

        calendar = self._get_calendar()
        event = self._get_event_by_reference(calendar, external_event_id)
        uid = str(uuid.uuid4())
        vobject_instance = getattr(event, "vobject_instance", None)
        vevent = getattr(vobject_instance, "vevent", None)
        if vevent and getattr(vevent, "uid", None) and getattr(vevent.uid, "value", None):
            uid = str(vevent.uid.value)

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
        event.data = ical
        event.save()
        return str(getattr(event, "url", external_event_id))


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
    company_context = _resolve_company_context(company_id)
    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    return {
        "integration": integration,
        "supported_providers": list_supported_calendar_providers(),
        "provider_readiness": readiness,
        "scheduling_profiles": _list_scheduling_profiles(company_context),
    }


def _layout_timeline_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segments:
        return []

    ordered_segments = sorted(
        segments,
        key=lambda item: (int(item["start_minutes"]), int(item["end_minutes"]), int(item["booking_id"])),
    )
    active_columns: list[dict[str, Any] | None] = []

    for segment in ordered_segments:
        start_minutes = int(segment["start_minutes"])

        assigned_column = None
        for index, active_segment in enumerate(active_columns):
            if active_segment is None or int(active_segment["end_minutes"]) <= start_minutes:
                assigned_column = index
                break

        if assigned_column is None:
            assigned_column = len(active_columns)
            active_columns.append(None)

        segment["column_index"] = assigned_column
        active_columns[assigned_column] = segment

    for segment in ordered_segments:
        overlapping_count = 1
        for candidate in ordered_segments:
            if candidate["booking_id"] == segment["booking_id"]:
                continue
            if int(candidate["start_minutes"]) < int(segment["end_minutes"]) and int(candidate["end_minutes"]) > int(segment["start_minutes"]):
                overlapping_count += 1
        segment["column_count"] = overlapping_count

    return ordered_segments


def get_calendar_timeline(
    *,
    company_id: int | None = None,
    start_date: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    normalized_days = max(1, min(days, 14))
    company_context = _resolve_company_context(company_id)
    timezone_obj = _get_company_timezone(company_id=company_id, company_context=company_context)
    timeline_start = date.fromisoformat(start_date) if start_date else datetime.now(timezone_obj).date()
    timeline_end = timeline_start + timedelta(days=normalized_days)

    range_start = datetime.combine(timeline_start, time.min, tzinfo=timezone_obj)
    range_end = datetime.combine(timeline_end, time.min, tzinfo=timezone_obj)

    bookings = repository.get_appointment_bookings_between(
        starts_at=range_start.astimezone(UTC).isoformat(),
        ends_at=range_end.astimezone(UTC).isoformat(),
        company_id=company_id,
    )

    day_buckets: dict[str, list[dict[str, Any]]] = {
        (timeline_start + timedelta(days=offset)).isoformat(): []
        for offset in range(normalized_days)
    }

    for booking in bookings:
        starts_at = datetime.fromisoformat(str(booking["starts_at"])).astimezone(timezone_obj)
        ends_at = datetime.fromisoformat(str(booking["ends_at"])).astimezone(timezone_obj)
        current_day = starts_at.date()
        last_day = ends_at.date()

        while current_day <= last_day:
            day_key = current_day.isoformat()
            if day_key not in day_buckets:
                current_day += timedelta(days=1)
                continue

            day_start = datetime.combine(current_day, time.min, tzinfo=timezone_obj)
            day_end = day_start + timedelta(days=1)
            segment_start = max(starts_at, day_start)
            segment_end = min(ends_at, day_end)
            day_buckets[day_key].append(
                {
                    "booking_id": int(booking["id"]),
                    "title": str(booking.get("title") or "Agendamento"),
                    "contact_id": booking.get("contact_id"),
                    "customer_profile": booking.get("customer_profile"),
                    "scheduling_profile_key": booking.get("scheduling_profile_key"),
                    "status": booking.get("status"),
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "segment_starts_at": segment_start.isoformat(),
                    "segment_ends_at": segment_end.isoformat(),
                    "start_minutes": int((segment_start - day_start).total_seconds() / 60),
                    "end_minutes": max(30, int((segment_end - day_start).total_seconds() / 60)),
                    "duration_minutes": max(30, int((segment_end - segment_start).total_seconds() / 60)),
                }
            )
            current_day += timedelta(days=1)

    days_payload = []
    for offset in range(normalized_days):
        current_day = timeline_start + timedelta(days=offset)
        day_key = current_day.isoformat()
        segments = _layout_timeline_segments(day_buckets[day_key])
        days_payload.append(
            {
                "date": day_key,
                "label": current_day.strftime("%d/%m"),
                "weekday_label": current_day.strftime("%A"),
                "booking_count": len(segments),
                "segments": segments,
            }
        )

    return {
        "timezone": str(timezone_obj),
        "start_date": timeline_start.isoformat(),
        "days": days_payload,
        "summary": {
            "booking_count": len(bookings),
            "confirmed_count": len([item for item in bookings if str(item.get("status") or "").lower() == "confirmed"]),
            "pending_count": len([item for item in bookings if str(item.get("status") or "").lower() == "pending"]),
        },
    }


def query_calendar_availability(
    *,
    company_id: int | None,
    start_date: str,
    end_date: str,
    duration_minutes: int,
    limit: int = 12,
    scheduling_profile_key: str | None = None,
    customer_profile: str | None = None,
    service_name: str | None = None,
    request_context: str | None = None,
    ignore_booking_id: int | None = None,
) -> dict[str, Any]:
    if duration_minutes <= 0:
        raise CalendarProviderError("duration_minutes must be greater than zero.")

    company_context = _resolve_company_context(company_id)
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        requested_profile_key=scheduling_profile_key,
        service_name=service_name,
        customer_profile=customer_profile,
        request_context=request_context,
    )
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
        ignore_booking_ids={ignore_booking_id} if ignore_booking_id is not None else None,
    )

    increment = _resolve_slot_increment_minutes(company_context, scheduling_profile)

    slots: list[dict[str, Any]] = []
    current_day = start_day
    while current_day <= end_day and len(slots) < limit:
        daily_windows = (
            _resolve_profile_daily_windows(
                current_day=current_day,
                scheduling_profile=scheduling_profile,
                company_context=company_context,
            )
            if scheduling_profile
            else _resolve_workday_windows(current_day, company_context)
        )
        if not daily_windows:
            current_day += timedelta(days=1)
            continue

        for work_start, work_end in daily_windows:
            slot_cursor = datetime.combine(current_day, work_start, tzinfo=timezone_obj)
            workday_end = datetime.combine(current_day, work_end, tzinfo=timezone_obj)

            while slot_cursor + timedelta(minutes=duration_minutes) <= workday_end and len(slots) < limit:
                slot_end = slot_cursor + timedelta(minutes=duration_minutes)
                has_conflict = any(
                    _intervals_overlap(slot_cursor, slot_end, interval.starts_at, interval.ends_at)
                    for interval in busy_intervals
                )
                if not has_conflict:
                    slots.append(
                        {
                            **_to_provider_slot(slot_cursor, duration_minutes, str(integration["provider"])),
                            "scheduling_profile_key": scheduling_profile.get("key") if scheduling_profile else None,
                            "customer_profile": customer_profile,
                        }
                    )
                slot_cursor += timedelta(minutes=increment)

        current_day += timedelta(days=1)

    return {
        "company_id": company_id,
        "provider": integration["provider"],
        "integration_source": integration["source"],
        "slots": slots,
        "provider_readiness": readiness,
        "scheduling_profile": scheduling_profile,
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
    scheduling_profile_key: str | None = None,
    customer_profile: str | None = None,
    service_name: str | None = None,
    request_context: str | None = None,
    ignore_booking_id: int | None = None,
) -> dict[str, Any]:
    company_context = _resolve_company_context(company_id)
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        requested_profile_key=scheduling_profile_key,
        service_name=service_name,
        customer_profile=customer_profile,
        request_context=request_context,
    )
    timezone_value = timezone_name or str(_get_company_timezone(company_context=company_context))
    starts_at_dt = _parse_iso_datetime(starts_at, timezone_value)
    ends_at_dt = _parse_iso_datetime(ends_at, timezone_value)
    if ends_at_dt <= starts_at_dt:
        raise CalendarProviderError("ends_at must be greater than starts_at.")

    _validate_booking_window(
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
        company_context=company_context,
        scheduling_profile=scheduling_profile,
    )

    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    if not readiness["ready"] and integration["provider"] != "mock":
        raise CalendarProviderError(str(readiness["details"]))

    local_conflicts = _get_local_busy_intervals(
        company_id,
        starts_at_dt,
        ends_at_dt,
        ignore_booking_ids={ignore_booking_id} if ignore_booking_id is not None else None,
    )
    if any(_intervals_overlap(starts_at_dt, ends_at_dt, item.starts_at, item.ends_at) for item in local_conflicts):
        raise CalendarProviderError("O horário solicitado conflita com um agendamento já existente.")

    if ignore_booking_id is None:
        provider_conflicts = provider.list_busy_intervals(starts_at_dt, ends_at_dt)
        if any(_intervals_overlap(starts_at_dt, ends_at_dt, item.starts_at, item.ends_at) for item in provider_conflicts):
            raise CalendarProviderError("O horário solicitado conflita com um evento já existente no calendário externo.")

    billing_decision: dict[str, Any] | None = None
    if company_id is not None:
        try:
            billing_decision = require_usage_capacity(
                company_id=int(company_id),
                metric="bookings",
            )
        except BillingLimitError as exc:
            raise CalendarProviderError(str(exc)) from exc

    try:
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
            scheduling_profile_key=scheduling_profile.get("key") if scheduling_profile else None,
            customer_profile=customer_profile,
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
        # `trigger.appointmentBooked` está na paleta do editor. Sem esta
        # emissão, um fluxo publicado com esse gatilho nunca dispararia — o
        # produto ofereceria um gatilho morto.
        #
        # Depois do commit do booking, e não dentro dele: a reserva já
        # aconteceu, e `emit_event` é idempotente por `event_key`, então uma
        # reentrega encontra o evento existente em vez de inscrever de novo.
        if company_id is not None:
            emit_appointment_booked(
                company_id=int(company_id),
                appointment_id=booking_id,
                contact_id=contact_id,
            )
        return {
            **booking,
            "provider_readiness": readiness,
            "scheduled_reminders": scheduled_reminders,
            "scheduling_profile": scheduling_profile,
        }
    except Exception:
        if company_id is not None and billing_decision is not None:
            refund_usage_reservation(
                company_id=int(company_id),
                decision=billing_decision,
            )
        raise


def _sync_booking_cancellation(
    *,
    booking: dict[str, Any],
    provider: BaseCalendarProvider,
    integration: dict[str, Any],
) -> dict[str, Any]:
    external_event_id = str(booking.get("external_event_id") or "").strip()
    if not external_event_id or str(integration.get("provider") or "mock") == "mock":
        return {
            "attempted": False,
            "status": "not_required",
            "provider": integration.get("provider"),
            "external_event_id": external_event_id or None,
        }

    readiness = provider.readiness()
    if not readiness["ready"]:
        raise CalendarProviderError(str(readiness["details"]))

    provider.cancel_external_event(external_event_id=external_event_id)
    return {
        "attempted": True,
        "status": "cancelled",
        "provider": integration.get("provider"),
        "external_event_id": external_event_id,
    }


def cancel_calendar_booking(
    *,
    booking_id: int,
    company_id: int | None = None,
) -> dict[str, Any]:
    booking = repository.get_appointment_booking(booking_id, company_id=company_id)
    if not booking:
        raise CalendarProviderError("Booking not found.")
    if str(booking.get("status") or "") not in {"confirmed", "pending"}:
        raise CalendarProviderError("Only confirmed or pending bookings can be cancelled.")

    booking_company_id = booking.get("company_id")
    integration = _resolve_calendar_integration(company_id=booking_company_id)
    provider = _get_provider(integration)
    external_sync = _sync_booking_cancellation(
        booking=booking,
        provider=provider,
        integration=integration,
    )
    repository.update_appointment_booking_status(
        booking_id,
        "cancelled",
        company_id=booking_company_id,
    )
    cancelled_reminders = repository.cancel_customer_reminders_for_booking(
        booking_id,
        company_id=booking_company_id,
    )

    return {
        "booking": repository.get_appointment_booking(
            booking_id,
            company_id=booking_company_id,
        ),
        "cancelled_reminders": cancelled_reminders,
        "external_sync": external_sync,
    }


def reschedule_calendar_booking(
    *,
    booking_id: int,
    starts_at: str,
    ends_at: str,
    timezone_name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    scheduling_profile_key: str | None = None,
    customer_profile: str | None = None,
    request_context: str | None = None,
    company_id: int | None = None,
) -> dict[str, Any]:
    booking = repository.get_appointment_booking(booking_id, company_id=company_id)
    if not booking:
        raise CalendarProviderError("Booking not found.")
    if str(booking.get("status") or "") not in {"confirmed", "pending"}:
        raise CalendarProviderError("Only confirmed or pending bookings can be rescheduled.")

    company_id = booking.get("company_id")
    company_context = _resolve_company_context(company_id)
    resolved_timezone_name = timezone_name or str(booking.get("timezone") or _get_company_timezone(company_context=company_context))
    starts_at_dt = _parse_iso_datetime(starts_at, resolved_timezone_name)
    ends_at_dt = _parse_iso_datetime(ends_at, resolved_timezone_name)
    if ends_at_dt <= starts_at_dt:
        raise CalendarProviderError("ends_at must be greater than starts_at.")

    resolved_title = (title or str(booking.get("title") or "")).strip() or "Agendamento"
    resolved_description = description if description is not None else booking.get("description")
    resolved_customer_profile = customer_profile if customer_profile is not None else booking.get("customer_profile")
    scheduling_profile = resolve_scheduling_profile(
        company_context=company_context,
        requested_profile_key=scheduling_profile_key or booking.get("scheduling_profile_key"),
        customer_profile=resolved_customer_profile,
        request_context=request_context,
    )

    _validate_booking_window(
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
        company_context=company_context,
        scheduling_profile=scheduling_profile,
    )

    integration = _resolve_calendar_integration(company_id=company_id)
    provider = _get_provider(integration)
    readiness = provider.readiness()
    if not readiness["ready"] and integration["provider"] != "mock":
        raise CalendarProviderError(str(readiness["details"]))

    local_conflicts = _get_local_busy_intervals(
        company_id,
        starts_at_dt,
        ends_at_dt,
        ignore_booking_ids={booking_id},
    )
    if any(_intervals_overlap(starts_at_dt, ends_at_dt, item.starts_at, item.ends_at) for item in local_conflicts):
        raise CalendarProviderError("O horário solicitado conflita com um agendamento já existente.")

    external_event_id = provider.update_external_event(
        external_event_id=str(booking.get("external_event_id") or "").strip() or None,
        title=resolved_title,
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
        timezone_name=resolved_timezone_name,
        description=str(resolved_description) if resolved_description is not None else None,
    )
    repository.update_appointment_booking_schedule(
        booking_id,
        starts_at=_to_utc_iso(starts_at_dt),
        ends_at=_to_utc_iso(ends_at_dt),
        timezone=resolved_timezone_name,
        external_event_id=external_event_id,
        scheduling_profile_key=scheduling_profile.get("key") if scheduling_profile else None,
        customer_profile=str(resolved_customer_profile) if resolved_customer_profile is not None else None,
        title=resolved_title,
        description=str(resolved_description) if resolved_description is not None else None,
        company_id=company_id,
    )
    cancelled_reminders = repository.cancel_customer_reminders_for_booking(
        booking_id,
        company_id=company_id,
    )
    updated_booking = repository.get_appointment_booking(booking_id, company_id=company_id)
    if not updated_booking:
        raise CalendarProviderError("Booking not found after reschedule.")
    scheduled_reminders = schedule_booking_customer_reminders(updated_booking)
    return {
        **updated_booking,
        "provider_readiness": readiness,
        "scheduled_reminders": scheduled_reminders,
        "cancelled_reminders": cancelled_reminders,
        "scheduling_profile": scheduling_profile,
        "external_sync": {
            "attempted": bool(str(booking.get("external_event_id") or "").strip() or external_event_id),
            "status": "updated" if external_event_id else "local_only",
            "provider": integration.get("provider"),
            "external_event_id": external_event_id,
        },
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
    integration = repository.get_calendar_integration(
        integration_id,
        company_id=company_id,
    )
    if not integration:
        raise CalendarProviderError("Calendar integration not found after creation.")
    return integration


def get_calendar_integrations(company_id: int | None = None) -> list[dict[str, Any]]:
    return repository.get_calendar_integrations(company_id=company_id)


def get_calendar_bookings(company_id: int | None = None) -> list[dict[str, Any]]:
    return repository.get_appointment_bookings(company_id=company_id)
