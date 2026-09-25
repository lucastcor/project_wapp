"""The onboarding wizard applied to the company the caller already owns.

Self-service signup creates a tenant before the owner has described the
business. `/admin/onboarding/setup` is reserved for platform operators and
creates a *new* company, so an owner working alone was stuck on the global
demo template. `/admin/onboarding/configure` is the tenant-scoped path.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.company_config_service import get_company_config
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()


def _register(client: TestClient, monkeypatch, *, suffix: str) -> tuple[dict, str]:
    monkeypatch.setattr(settings, "enable_self_service_signup", True)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    response = client.post(
        "/platform/auth/register",
        json={
            "email": f"owner-{suffix}@example.com",
            "password": "Configure!Secure2026",
            "display_name": "Tenant Owner",
            "company_name": f"Signup Company {suffix}",
            "company_slug": f"signup-{suffix}",
        },
    )
    assert response.status_code == 200, response.text
    csrf_token = client.cookies.get("project_wapp_csrf")
    assert csrf_token
    return response.json(), str(csrf_token)


def _wizard_payload(name: str) -> dict:
    return {
        "company_name": name,
        "business_type_key": "salao",
        "segment": "Estética facial",
        "phone": "+55 62 99999-0000",
        "timezone": "America/Sao_Paulo",
        "weekday_hours": "09:00-19:00",
        "saturday_hours": "09:00-13:00",
        "sunday_hours": "fechado",
        "slot_duration_minutes": 45,
        "minimum_notice_minutes": 60,
        "reminder_offsets_days": [1],
        "custom_rules": ["Não inventar preços"],
        "services": [{"name": "Limpeza de pele", "duration_minutes": 60, "price": 150.0}],
    }


def test_identity_owner_configures_and_publishes_own_company(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = int(payload["memberships"][0]["company_id"])

        before = get_company_config(company_id, use_cache=False)
        assert before.source == "global_template"

        response = client.post(
            "/admin/onboarding/configure",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json=_wizard_payload("Studio Bela Pele"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["onboarding_complete"] is True
        assert body["company"]["id"] == company_id
        assert body["company"]["name"] == "Studio Bela Pele"
        assert body["company"]["business_type"] == "salao"
        # The slug is the public identifier of the tenant and must not move.
        assert body["company"]["slug"] == f"signup-{suffix}"
        assert body["next_steps"], "next steps guide the owner to go live"

        after = get_company_config(company_id, use_cache=False)
        assert after.source == "database"
        assert after.status == "published"
        assert after.company_name == "Studio Bela Pele"
        items = after.context.get("catalog", {}).get("items", [])
        assert [item["name"] for item in items] == ["Limpeza de pele"]


def test_identity_owner_cannot_configure_a_company_they_do_not_belong_to(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    hidden_company_id = repository.create_company(slug=f"hidden-{suffix}", name="Hidden Company")
    with TestClient(app) as client:
        _payload, csrf_token = _register(client, monkeypatch, suffix=suffix)

        denied = client.post(
            "/admin/onboarding/configure",
            params={"company_id": hidden_company_id},
            headers={"X-CSRF-Token": csrf_token},
            json=_wizard_payload("Takeover"),
        )
        assert denied.status_code == 403

        unscoped = client.post(
            "/admin/onboarding/configure",
            headers={"X-CSRF-Token": csrf_token},
            json=_wizard_payload("No scope"),
        )
        assert unscoped.status_code == 400

    hidden = repository.get_company(hidden_company_id)
    assert hidden is not None and hidden["name"] == "Hidden Company"


def test_platform_setup_stays_reserved_for_break_glass(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        _payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        response = client.post(
            "/admin/onboarding/setup",
            headers={"X-CSRF-Token": csrf_token},
            json=_wizard_payload(f"Another {suffix}"),
        )
        assert response.status_code == 403


def test_configure_rejects_unknown_business_type(monkeypatch) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = int(payload["memberships"][0]["company_id"])
        response = client.post(
            "/admin/onboarding/configure",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={**_wizard_payload("Studio"), "business_type_key": "padaria"},
        )
        assert response.status_code == 400
        assert "business_type_key" in response.json()["detail"]
