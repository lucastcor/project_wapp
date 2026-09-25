"""P07 — credencial da agenda externa nunca sai pela API.

Achado ao implementar "segredos mascarados": o perfil de agenda e a listagem
de integrações (leitura de viewer) devolviam `config` como gravado, com
`client_secret` e `refresh_token` do Google.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import calendar_service

client = TestClient(app)

SECRET = "segredo-sintetico-XYZ987"
REFRESH_TOKEN = "refresh-sintetico-ABC654"
CALENDAR_ID = "agenda-clinica@example.invalid"


@pytest.fixture(autouse=True)
def _session_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "legacy-viewer")
    monkeypatch.setattr(settings, "admin_operator_token", "legacy-operator")
    monkeypatch.setattr(settings, "admin_superadmin_token", "legacy-superadmin")
    monkeypatch.setattr(settings, "admin_api_token", None)


def _headers(principal: dict) -> dict[str, str]:
    return {
        "Cookie": f"project_wapp_platform_session={principal['token']}; project_wapp_csrf={principal['csrf']}",
        "X-CSRF-Token": principal["csrf"],
    }


@pytest.fixture
def google_integration(company_factory, admin_principal_factory) -> dict:
    company_id = company_factory("calendar-secrets", business_type="services")
    admin = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    viewer = admin_principal_factory(global_role="viewer", company_ids=[company_id])
    created = client.post(
        "/admin/calendar/integrations",
        params={"company_id": company_id},
        json={
            "provider": "google_calendar",
            "external_calendar_id": CALENDAR_ID,
            "config": {
                "calendar_id": CALENDAR_ID,
                "client_id": "client-id-sintetico-1234",
                "client_secret": SECRET,
                "refresh_token": REFRESH_TOKEN,
            },
            "active": True,
        },
        headers=_headers(admin),
    )
    return {"company_id": company_id, "viewer": viewer, "created": created}


def _assert_no_credentials(text: str) -> None:
    assert SECRET not in text
    assert REFRESH_TOKEN not in text
    assert CALENDAR_ID not in text


def test_creating_an_integration_does_not_echo_its_credentials(google_integration) -> None:
    created = google_integration["created"]

    assert created.status_code == 200, created.text
    _assert_no_credentials(created.text)


def test_viewer_reads_integrations_and_profile_without_credentials(google_integration) -> None:
    company_id = google_integration["company_id"]
    headers = _headers(google_integration["viewer"])

    listed = client.get("/admin/calendar/integrations", params={"company_id": company_id}, headers=headers)
    profile = client.get("/admin/calendar/profile", params={"company_id": company_id}, headers=headers)

    assert listed.status_code == 200, listed.text
    assert profile.status_code == 200, profile.text
    _assert_no_credentials(listed.text)
    _assert_no_credentials(profile.text)
    config = profile.json()["integration"]["config"]
    assert config["client_secret_present"] is True
    assert config["refresh_token_present"] is True
    assert config["calendar_id"].endswith(CALENDAR_ID[-4:])
    assert profile.json()["integration"]["provider"] == "google_calendar"


def test_the_provider_still_receives_the_full_configuration(google_integration) -> None:
    internal = calendar_service._resolve_calendar_integration(company_id=google_integration["company_id"])

    assert internal["config"]["client_secret"] == SECRET
    assert internal["config"]["refresh_token"] == REFRESH_TOKEN
