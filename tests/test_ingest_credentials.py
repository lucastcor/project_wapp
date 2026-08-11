"""Autenticação tenant-scoped da API de ingestão de mensagens."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings
from app.main import app
from app.services import tenant_credential_service
from app.services.tenant_credential_service import (
    TenantCredentialError,
    authenticate_tenant_credential,
    issue_tenant_credential,
    list_tenant_credentials,
    revoke_tenant_credential,
)
from app.storage import identity_repository

client = TestClient(app)

PAYLOAD = {"contact_id": "5511999999999", "contact_name": "Cliente", "message": "oi"}


@pytest.fixture(autouse=True)
def _stub_processing(monkeypatch):
    captured: dict = {}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return {
            "contact_id": kwargs["contact_id"],
            "company_id": kwargs.get("company_id"),
            "company_slug": kwargs.get("company_slug"),
            "classification": "lead_novo",
            "classification_reason": "teste",
            "classification_confidence": 0.9,
            "reply": "ok",
            "source": "template",
            "auto_reply": True,
            "action": "auto_reply",
            "priority": "high",
            "pipeline_stage": "novo_lead",
            "next_action": "seguir",
            "lead_temperature": "warm",
            "handoff_required": False,
            "pending_question_id": None,
            "question_type": None,
        }

    monkeypatch.setattr(routes, "process_incoming_message", fake_process)
    return captured


def test_request_without_credential_is_rejected() -> None:
    response = client.post("/messages/incoming", json=PAYLOAD)

    assert response.status_code == 401
    assert "credential is required" in response.json()["detail"]


def test_invalid_credential_is_rejected() -> None:
    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"X-Tenant-Token": "wapp_token-que-nao-existe"},
    )

    assert response.status_code == 401


def test_revoked_credential_is_rejected(tenant_credential_factory, company_factory) -> None:
    company_id = company_factory("revoke")
    credential = tenant_credential_factory(company_id)

    assert revoke_tenant_credential(
        company_id=company_id,
        credential_id=credential["credential_id"],
    )

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"X-Tenant-Token": credential["token"]},
    )

    assert response.status_code == 401
    assert "revoked" in response.json()["detail"]


def test_expired_credential_is_rejected(company_factory) -> None:
    company_id = company_factory("expired")
    issued = issue_tenant_credential(
        company_id=company_id,
        description="expirada",
        expires_at="2000-01-01T00:00:00+00:00",
    )

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"X-Tenant-Token": issued["token"]},
    )

    assert response.status_code == 401


def test_credential_without_required_scope_is_rejected(company_factory, monkeypatch) -> None:
    company_id = company_factory("scope")
    monkeypatch.setattr(
        tenant_credential_service,
        "VALID_SCOPES",
        frozenset({"messages:write", "messages:read"}),
    )
    issued = issue_tenant_credential(company_id=company_id, scopes=["messages:read"])

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"X-Tenant-Token": issued["token"]},
    )

    assert response.status_code == 401
    assert "scope" in response.json()["detail"]


def test_credential_of_company_a_cannot_target_company_b(
    tenant_credential_factory,
    company_factory,
) -> None:
    company_a = company_factory("cred-a")
    company_b = company_factory("cred-b")
    credential = tenant_credential_factory(company_a)

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        params={"company_id": company_b},
        headers={"X-Tenant-Token": credential["token"]},
    )

    assert response.status_code == 403
    assert "does not match the authenticated credential" in response.json()["detail"]


def test_credential_processes_only_its_own_company(
    _stub_processing,
    tenant_credential_factory,
    company_factory,
) -> None:
    company_a = company_factory("cred-only-a")
    credential = tenant_credential_factory(company_a)

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"X-Tenant-Token": credential["token"]},
    )

    assert response.status_code == 200
    assert _stub_processing["company_id"] == company_a


def test_matching_company_hint_is_accepted(
    _stub_processing,
    tenant_credential_factory,
    company_factory,
) -> None:
    company_id = company_factory("cred-match")
    credential = tenant_credential_factory(company_id)

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        params={"company_id": company_id},
        headers={"X-Tenant-Token": credential["token"]},
    )

    assert response.status_code == 200
    assert _stub_processing["company_id"] == company_id


def test_bearer_authorization_is_accepted(
    _stub_processing,
    tenant_credential_factory,
    company_factory,
) -> None:
    company_id = company_factory("cred-bearer")
    credential = tenant_credential_factory(company_id)

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        headers={"Authorization": f"Bearer {credential['token']}"},
    )

    assert response.status_code == 200
    assert _stub_processing["company_id"] == company_id


def test_no_fallback_to_first_active_company(company_factory) -> None:
    # Existem empresas ativas, mas sem credencial não há empresa alguma.
    company_factory("fallback-guard")

    response = client.post("/messages/incoming", json=PAYLOAD)

    assert response.status_code == 401


def test_unauthenticated_ingest_requires_explicit_configuration(
    _stub_processing,
    monkeypatch,
    company_factory,
) -> None:
    company_factory("dev-ergonomics")
    monkeypatch.setattr(settings, "allow_unauthenticated_ingest", True)

    response = client.post(
        "/messages/incoming",
        json=PAYLOAD,
        # Mesmo no modo permissivo, o chamador não escolhe empresa.
        params={"company_id": 999999},
    )

    assert response.status_code == 200
    assert _stub_processing["company_id"] != 999999


def test_unauthenticated_ingest_is_blocked_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "allow_unauthenticated_ingest", True)
    monkeypatch.setattr(routes, "is_production_like", lambda: True)

    response = client.post("/messages/incoming", json=PAYLOAD)

    assert response.status_code == 401


def test_token_never_appears_in_logs_errors_or_storage(
    caplog,
    company_factory,
) -> None:
    company_id = company_factory("cred-secrecy")

    with caplog.at_level(logging.DEBUG):
        issued = issue_tenant_credential(company_id=company_id, description="segredo")
        response = client.post(
            "/messages/incoming",
            json=PAYLOAD,
            params={"company_id": company_id + 100000},
            headers={"X-Tenant-Token": issued["token"]},
        )

    token = issued["token"]
    assert token not in caplog.text
    assert token not in response.text

    listed = list_tenant_credentials(company_id=company_id)
    assert token not in str(listed)
    assert all("token_hash" not in credential for credential in listed)

    # O que é persistido é o hash, nunca o segredo.
    stored = identity_repository.get_tenant_credential_by_hash(
        tenant_credential_service.hash_token(token)
    )
    assert stored is not None
    assert stored["token_hash"] != token


def test_authenticate_helper_fails_closed() -> None:
    with pytest.raises(TenantCredentialError):
        authenticate_tenant_credential(None)
    with pytest.raises(TenantCredentialError):
        authenticate_tenant_credential("")
    with pytest.raises(TenantCredentialError):
        authenticate_tenant_credential("   ")
