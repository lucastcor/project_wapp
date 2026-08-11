"""Segurança das superfícies HTTP: SSE, RBAC, webhook e resolução de tenant."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings
from app.main import app
from app.services import tenant_resolution_service
from app.services.realtime_service import broadcast_event, hub
from app.storage import channel_repository

client = TestClient(app)


def _meta_payload(*, phone_number_id: str, message_id: str = "wamid.security.1") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": "5511999990000", "profile": {"name": "Cliente"}}],
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": "5511999990000",
                                    "type": "text",
                                    "text": {"body": "Olá"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# --- SSE ---------------------------------------------------------------------------


def test_sse_requires_authentication(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_superadmin_token", "super-token")

    with TestClient(app) as local_client:
        response = local_client.get("/platform/events")

    assert response.status_code == 401


def test_sse_requires_company_scope_for_non_superadmin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_api_token", None)

    with TestClient(app) as local_client:
        login = local_client.post(
            "/platform/auth/session",
            json={"admin_token": "viewer-token", "admin_actor": "viewer"},
        )
        assert login.status_code == 200

        response = local_client.get("/platform/events")

    # Token legado por papel não carrega membership: nega antes de abrir o
    # stream, em vez de cair em escopo global.
    assert response.status_code == 403
    assert response.json()["detail"] == "Company access denied for this principal."


def test_sse_events_are_scoped_by_company() -> None:
    subscription_a = hub.subscribe(company_id=101, role="operator")
    subscription_b = hub.subscribe(company_id=202, role="operator")

    broadcast_event("new_message", {"classification": "lead_novo"}, company_id=101)

    assert subscription_a.queue.qsize() == 1
    assert subscription_b.queue.qsize() == 0

    hub.unsubscribe(subscription_a)
    hub.unsubscribe(subscription_b)


def test_sse_never_transmits_contact_identifiers_in_clear_text() -> None:
    subscription = hub.subscribe(company_id=303, role="operator")

    broadcast_event(
        "new_message",
        {"contact_id": "5511987654321", "classification": "lead_novo"},
        company_id=303,
    )
    payload = json.loads(subscription.queue.get_nowait())
    hub.unsubscribe(subscription)

    assert payload["data"]["contact_id"] != "5511987654321"
    assert "5511987654321" not in json.dumps(payload)


def test_global_subscriber_receives_company_events() -> None:
    subscription = hub.subscribe(company_id=None, role="superadmin")

    broadcast_event("new_booking", {"booking_id": 1}, company_id=404)
    assert subscription.queue.qsize() == 1

    hub.unsubscribe(subscription)


# --- webhook -------------------------------------------------------------------------


def test_normalized_payload_is_blocked_in_production(monkeypatch) -> None:
    monkeypatch.setattr(routes, "is_production_like", lambda: True)

    response = client.post(
        "/webhooks/whatsapp",
        json={
            "event": "message.received",
            "from_number": "5511999990000",
            "text": "Olá",
        },
    )

    assert response.status_code == 403
    assert "Normalized webhook payload is disabled" in response.json()["detail"]


def test_normalized_payload_requires_hmac_when_app_secret_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "segredo-do-app")

    response = client.post(
        "/webhooks/whatsapp",
        json={
            "event": "message.received",
            "from_number": "5511999990000",
            "text": "Olá",
        },
    )

    assert response.status_code == 403
    assert "signature" in response.json()["detail"].lower()


def test_normalized_payload_accepts_a_valid_signature(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "segredo-do-app")
    monkeypatch.setattr(
        routes,
        "process_whatsapp_webhook",
        lambda **kwargs: {"contact_id": kwargs["from_number"], "auto_reply": False, "reply": ""},
    )

    body = json.dumps(
        {"event": "message.received", "from_number": "5511999990000", "text": "Olá"}
    ).encode("utf-8")

    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "segredo-do-app"),
        },
    )

    assert response.status_code == 200


def test_production_webhook_requires_a_channel_binding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "segredo-do-app")
    monkeypatch.setattr(tenant_resolution_service, "is_production_like", lambda: True)

    payload = _meta_payload(phone_number_id="sem-vinculo", message_id="wamid.unbound.1")
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "segredo-do-app"),
        },
    )

    assert response.status_code == 403
    assert "No active channel binding" in response.json()["detail"]


def test_channel_binding_resolves_the_tenant_ignoring_caller_hints(
    monkeypatch,
    company_factory,
) -> None:
    bound_company = company_factory("bound-company")
    other_company = company_factory("other-company")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "segredo-do-app")

    channel_repository.create_channel_binding(
        company_id=bound_company,
        provider="whatsapp",
        external_id="phone-bound-001",
        label="Número principal",
    )

    captured: dict = {}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return {"contact_id": kwargs["from_number"], "auto_reply": False, "reply": ""}

    monkeypatch.setattr(routes, "process_whatsapp_webhook", fake_process)

    payload = _meta_payload(phone_number_id="phone-bound-001", message_id="wamid.bound.1")
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhooks/whatsapp",
        content=body,
        # O chamador tenta forçar outra empresa; o binding do canal vence.
        params={"company_id": other_company},
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, "segredo-do-app"),
        },
    )

    assert response.status_code == 200
    assert captured["company_id"] == bound_company


def test_meta_payload_still_requires_a_valid_signature() -> None:
    payload = _meta_payload(phone_number_id="phone-x", message_id="wamid.sig.1")

    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code in {403, 503}


# --- RBAC ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("post", "/admin/analytics/bookings/1/outcome", {"booking_outcome": "attended"}),
        ("post", "/admin/onboarding/setup", {"company_name": "X", "company_slug": "x"}),
        ("post", "/admin/billing/trial", {}),
        ("post", "/admin/billing/cancel", {}),
        ("post", "/admin/billing/usage/record", {}),
        ("post", "/admin/knowledge/documents", {"title": "t", "content": "c"}),
        ("post", "/admin/dataset-versions", {}),
        ("post", "/admin/models", {"model_ref": "ft:x"}),
        ("post", "/admin/models/rollback", None),
        ("post", "/admin/channel-bindings", {"external_id": "abc"}),
    ],
)
def test_viewer_cannot_execute_privileged_mutations(monkeypatch, method, path, json_body) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-token")
    monkeypatch.setattr(settings, "admin_operator_token", "operator-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-token")
    monkeypatch.setattr(settings, "admin_api_token", None)

    response = getattr(client, method)(
        path,
        json=json_body if json_body is not None else None,
        headers={"X-Admin-Token": "viewer-token"},
    )

    # Um viewer legado é barrado duas vezes: não tem empresa autorizada e não
    # tem papel para mutação. Qualquer uma das negativas é aceitável aqui; a
    # separação entre elas é coberta em test_admin_tenant_authorization.py.
    assert response.status_code == 403
    assert response.json()["detail"] in {
        "Insufficient admin privileges.",
        "Company access denied for this principal.",
        # Criar empresa é operação de plataforma (T-05): a política central
        # recusa antes mesmo da checagem de papel.
        "This operation requires a global superadmin principal.",
    }


def test_unauthenticated_request_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-token")

    response = client.get("/admin/taxonomy", headers={"X-Admin-Token": "token-invalido"})

    assert response.status_code == 401


def test_platform_endpoints_require_a_resolved_company(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", False)

    response = client.get("/admin/knowledge/documents", params={"company_slug": "nao-existe"})

    assert response.status_code == 400
    assert "company_id or company_slug" in response.json()["detail"]
