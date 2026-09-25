"""P07 connection diagnostics use runtime bindings and never expose credentials."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import calendar_service
from app.services.calendar_service import CalendarProviderError, GoogleCalendarProvider
from app.storage.repository import RuntimeRepository, _iso_timestamp

client = TestClient(app)
repository = RuntimeRepository()


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {
        "Cookie": f"project_wapp_platform_session={principal['token']}; project_wapp_csrf={principal['csrf']}",
        "X-CSRF-Token": principal["csrf"],
    }


def test_runtime_claim_timestamps_have_fixed_width_for_sqlite_ordering() -> None:
    assert _iso_timestamp(datetime(2026, 9, 14, 12, 0, tzinfo=UTC)) == "2026-09-14T12:00:00.000000+00:00"


def test_whatsapp_status_reads_canonical_runtime_binding_and_masks_it(
    company_factory,
    admin_principal_factory,
    monkeypatch,
) -> None:
    company_id = company_factory("wa-status")
    phone_number_id = f"meta-phone-{uuid4().hex}"
    display_number = "551199998877"
    repository.upsert_whatsapp_binding(
        company_id=company_id,
        phone_number_id=phone_number_id,
        display_phone_number=display_number,
        updated_by="test",
    )
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_id])
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.facebook.example/v22.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", "synthetic-meta-token-123")
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "synthetic-verify-token-456")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "synthetic-app-secret-789")

    response = client.get(
        "/admin/whatsapp/channel-status",
        params={"company_id": company_id},
        headers=_headers(viewer),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is True
    assert payload["active_binding_count"] == 1
    assert payload["binding"]["phone_number_id"].endswith(phone_number_id[-4:])
    assert payload["binding"]["display_phone_number"].endswith(display_number[-4:])
    assert phone_number_id not in response.text
    assert display_number not in response.text
    assert "synthetic-meta-token-123" not in response.text
    assert "synthetic-app-secret-789" not in response.text


def test_whatsapp_connection_check_is_read_only_and_sanitized(
    company_factory,
    admin_principal_factory,
    monkeypatch,
) -> None:
    company_id = company_factory("wa-check")
    phone_number_id = f"meta-phone-{uuid4().hex}"
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=phone_number_id)
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    token = "synthetic-meta-token-never-return"
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.facebook.example/v22.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", token)

    observed: dict = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"id": phone_number_id, "verified_name": "Synthetic Company"}

    def _get(url, *, headers, params, timeout):
        observed.update(url=url, headers=headers, params=params, timeout=timeout)
        return _Response()

    import requests

    monkeypatch.setattr(requests, "get", _get)
    response = client.post(
        "/admin/whatsapp/test-connection",
        params={"company_id": company_id},
        headers=_headers(owner),
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert response.json()["configured"] is True
    assert observed["url"].endswith(f"/{phone_number_id}")
    assert observed["params"] == {"fields": "id,display_phone_number,verified_name"}
    assert "messages" not in observed["url"]
    assert phone_number_id not in response.text
    assert token not in response.text


def test_whatsapp_connection_failure_does_not_echo_provider_message(
    company_factory,
    admin_principal_factory,
    monkeypatch,
) -> None:
    company_id = company_factory("wa-check-fail")
    phone_number_id = f"meta-phone-{uuid4().hex}"
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=phone_number_id)
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    token = "synthetic-token-in-provider-error"
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.facebook.example/v22.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", token)

    class _Response:
        status_code = 401

        @staticmethod
        def json() -> dict:
            return {"error": {"code": 190, "message": f"Invalid token {token} for {phone_number_id}"}}

    import requests

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response())
    response = client.post(
        "/admin/whatsapp/test-connection",
        params={"company_id": company_id},
        headers=_headers(owner),
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is False
    assert response.json()["provider_code"] == 190
    assert token not in response.text
    assert phone_number_id not in response.text


def test_calendar_connection_check_queries_availability_without_writing(
    company_factory,
    admin_principal_factory,
    monkeypatch,
) -> None:
    company_id = company_factory("calendar-check")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    calls: list[str] = []

    class _ReadOnlyProvider:
        def readiness(self) -> dict:
            return {"provider": "google_calendar", "ready": True, "details": "configured"}

        def list_busy_intervals(self, window_start, window_end) -> list:
            calls.append("list_busy_intervals")
            assert window_end > window_start
            return []

        def create_external_event(self, **kwargs):
            raise AssertionError("connection check must not create events")

    monkeypatch.setattr(
        calendar_service,
        "_resolve_calendar_integration",
        lambda company_id=None: {"provider": "google_calendar", "config": {}},
    )
    monkeypatch.setattr(calendar_service, "_get_provider", lambda integration: _ReadOnlyProvider())

    response = client.post(
        "/admin/calendar/test-connection",
        params={"company_id": company_id},
        headers=_headers(owner),
    )

    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert response.json()["mode"] == "external"
    assert calls == ["list_busy_intervals"]


def test_google_freebusy_embedded_error_is_not_treated_as_an_empty_calendar() -> None:
    class _Call:
        @staticmethod
        def execute() -> dict:
            return {
                "calendars": {
                    "calendar-test": {
                        "errors": [{"domain": "global", "reason": "notFound"}],
                        "busy": [],
                    }
                }
            }

    class _FreeBusy:
        @staticmethod
        def query(*, body):
            assert body["items"] == [{"id": "calendar-test"}]
            return _Call()

    class _Service:
        @staticmethod
        def freebusy():
            return _FreeBusy()

    provider = GoogleCalendarProvider({"provider": "google_calendar", "config": {}})
    provider._get_google_service = lambda: (_Service(), "calendar-test")  # type: ignore[method-assign]

    with pytest.raises(CalendarProviderError, match="disponibilidade"):
        provider.list_busy_intervals(
            datetime.now(UTC),
            datetime.now(UTC) + timedelta(minutes=1),
        )


def test_connection_checks_require_company_admin(
    company_factory,
    admin_principal_factory,
) -> None:
    company_id = company_factory("connection-rbac")
    operator = admin_principal_factory(global_role="operator", company_ids=[company_id])

    for path in ["/admin/calendar/test-connection", "/admin/whatsapp/test-connection"]:
        response = client.post(path, params={"company_id": company_id}, headers=_headers(operator))
        assert response.status_code == 403
