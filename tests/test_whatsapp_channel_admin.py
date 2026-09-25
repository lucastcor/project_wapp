"""P07 — templates WhatsApp aprovados e consentimento de contato pelo painel.

Sem template aprovado cadastrado, lembrete e oferta de vaga fora da janela de
24 h viram dead letter; sem tela, a empresa não teria como cadastrá-lo.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import channel_delivery_repository as channel

client = TestClient(app)

TEMPLATE = {
    "purpose": "reminders",
    "name": "lembrete_consulta",
    "language_code": "pt_BR",
    "category": "utility",
    "status": "approved",
    "parameter_count": 1,
    "active": True,
}


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
def tenant(company_factory, admin_principal_factory) -> dict:
    company_id = company_factory("whatsapp-admin", business_type="services")
    other_company = company_factory("whatsapp-other")
    return {
        "company_id": company_id,
        "viewer": admin_principal_factory(global_role="viewer", company_ids=[company_id]),
        "operator": admin_principal_factory(global_role="operator", company_ids=[company_id]),
        "admin": admin_principal_factory(global_role="superadmin", company_ids=[company_id]),
        "outsider": admin_principal_factory(global_role="superadmin", company_ids=[other_company]),
    }


def _put_template(tenant: dict, principal: str, body: dict):
    return client.put(
        "/admin/whatsapp/templates",
        params={"company_id": tenant["company_id"]},
        json=body,
        headers=_headers(tenant[principal]),
    )


def test_company_admin_registers_a_template_and_viewer_lists_it(tenant) -> None:
    created = _put_template(tenant, "admin", TEMPLATE)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "approved"

    updated = _put_template(tenant, "admin", {**TEMPLATE, "status": "paused"})
    assert updated.status_code == 200, updated.text

    listed = client.get(
        "/admin/whatsapp/templates", params={"company_id": tenant["company_id"]}, headers=_headers(tenant["viewer"])
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["language_code"] == "pt_BR"
    assert [(item["purpose"], item["status"]) for item in body["templates"]] == [("reminders", "paused")]


def test_template_writes_are_limited_to_company_admins_and_validated(tenant) -> None:
    assert _put_template(tenant, "operator", TEMPLATE).status_code == 403
    assert _put_template(tenant, "outsider", TEMPLATE).status_code in {403, 404}
    outsider_read = client.get(
        "/admin/whatsapp/templates", params={"company_id": tenant["company_id"]}, headers=_headers(tenant["outsider"])
    )
    assert outsider_read.status_code in {403, 404}

    for invalid in (
        {**TEMPLATE, "category": "marketing"},
        {**TEMPLATE, "status": "unknown"},
        # `reply` é finalidade válida: resposta que só sai depois de 24 h usa template.
        {**TEMPLATE, "purpose": "unknown_purpose"},
        {**TEMPLATE, "language_code": "portuguese"},
        {**TEMPLATE, "parameter_count": 11},
    ):
        response = _put_template(tenant, "admin", invalid)
        assert response.status_code == 422, (invalid, response.text)
    assert channel.list_templates(company_id=tenant["company_id"]) == []


def test_operator_records_consent_with_evidence_and_viewer_cannot(tenant) -> None:
    company_id = tenant["company_id"]
    formatted = quote("+55 (11) 90000-0456", safe="")
    url = f"/admin/whatsapp/contacts/{formatted}/consents/marketing"
    body = {"status": "granted", "evidence_ref": "formulario-sintetico-12"}

    assert client.put(url, params={"company_id": company_id}, json=body, headers=_headers(tenant["viewer"])).status_code == 403
    granted = client.put(url, params={"company_id": company_id}, json=body, headers=_headers(tenant["operator"]))
    assert granted.status_code == 200, granted.text
    assert channel.get_consent_status(company_id=company_id, contact_id="5511900000456", purpose="marketing") == "granted"

    state = client.get(
        "/admin/whatsapp/contacts/5511900000456/channel", params={"company_id": company_id}, headers=_headers(tenant["viewer"])
    )
    assert state.status_code == 200, state.text
    consents = state.json()["consents"]
    assert consents["marketing"] == "granted"
    assert consents["reminders"] is None
    assert "reply" not in consents

    invalid = client.put(
        "/admin/whatsapp/contacts/5511900000456/consents/reply",
        params={"company_id": company_id},
        json=body,
        headers=_headers(tenant["operator"]),
    )
    assert invalid.status_code == 422


def test_contact_channel_state_shows_the_customer_opt_out(tenant) -> None:
    company_id = tenant["company_id"]
    channel.set_opt_out(company_id=company_id, contact_id="5511900000457", source="whatsapp_keyword")

    state = client.get(
        "/admin/whatsapp/contacts/5511900000457/channel", params={"company_id": company_id}, headers=_headers(tenant["viewer"])
    )

    assert state.status_code == 200, state.text
    assert state.json()["opted_out_at"]
    assert state.json()["opt_out_source"] == "whatsapp_keyword"


def test_summary_warns_when_reminders_are_scheduled_without_an_approved_template(tenant) -> None:
    from datetime import UTC, datetime, timedelta

    from app.services.operations_summary_service import get_operations_summary
    from app.storage.repository import RuntimeRepository
    from app.storage.sql_support import execute_rowcount

    company_id = tenant["company_id"]
    contact_id = "5511900000458"
    RuntimeRepository().save_contact(contact_id, "Cliente", company_id=company_id)
    execute_rowcount(
        "INSERT INTO customer_reminders (company_id, contact_id, reminder_text, remind_at, timezone, status, audience) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 'customer')",
        (company_id, contact_id, "Lembrete sintético", (datetime.now(UTC) + timedelta(days=1)).isoformat(),
         "America/Sao_Paulo"),
    )

    alerts = get_operations_summary(company_id)["alerts"]
    assert {"code": "whatsapp_template_missing", "purposes": ["reminders"]} in alerts

    channel.upsert_template(company_id=company_id, **{**TEMPLATE, "status": "pending"})
    assert any(alert["code"] == "whatsapp_template_missing" for alert in get_operations_summary(company_id)["alerts"])

    channel.upsert_template(company_id=company_id, **TEMPLATE)
    assert all(alert["code"] != "whatsapp_template_missing" for alert in get_operations_summary(company_id)["alerts"])
