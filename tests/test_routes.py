import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import routes
from app.core.config import settings
from app.main import app
from app.services.rate_limit_service import clear_rate_limit_counters

client = TestClient(app)

NORMALIZED_INGRESS_SECRET = "test-internal-ingress-secret"
NORMALIZED_INGRESS_HEADERS = {"X-Internal-Ingress-Secret": NORMALIZED_INGRESS_SECRET}


def enable_normalized_ingress(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", NORMALIZED_INGRESS_SECRET)


def test_healthcheck() -> None:
    response = client.get("/health", headers={"X-Request-ID": "req-health-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "req-health-123"


def test_platform_shell() -> None:
    response = client.get("/platform")

    assert response.status_code == 200
    assert "WAPP Platform" in response.text
    assert "/platform/assets/app.js" in response.text


def test_platform_asset() -> None:
    response = client.get("/platform/assets/app.js")

    assert response.status_code == 200
    assert "bootstrap()" in response.text


def test_platform_session_login_when_admin_auth_disabled(monkeypatch) -> None:
    with TestClient(app) as local_client:
        monkeypatch.setattr(settings, "enable_admin_auth", False)

        response = local_client.post(
            "/platform/auth/session",
            json={"admin_actor": "platform-admin"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is True
        assert payload["role"] == "superadmin"
        assert "project_wapp_platform_session=" in response.headers["set-cookie"]


def test_platform_session_cookie_can_access_admin_routes(monkeypatch) -> None:
    class FakeRepository:
        def get_observability_summary(self) -> dict:
            return {
                "totals": {
                    "messages": 1,
                    "contacts": 1,
                    "pending_questions": 0,
                    "knowledge_entries": 0,
                    "message_examples": 0,
                    "outbound_messages": 0,
                    "finetuning_jobs": 0,
                    "companies": 1,
                },
                "pending_questions_by_status": {},
                "outbound_by_status": {},
                "knowledge_by_status": {},
                "messages_by_classification": {},
                "messages_by_action": {},
                "messages_by_reply_source": {},
                "companies_by_status": {"active": 1, "inactive": 0},
            }

        def create_audit_event(self, **kwargs) -> int:
            return 1

    from app.api import admin_routes

    with TestClient(app) as local_client:
        monkeypatch.setattr(settings, "enable_admin_auth", True)
        monkeypatch.setattr(settings, "admin_api_token", None)
        monkeypatch.setattr(settings, "admin_viewer_token", "viewer-cookie-token")
        monkeypatch.setattr(settings, "admin_operator_token", None)
        monkeypatch.setattr(settings, "admin_superadmin_token", None)
        monkeypatch.setattr(admin_routes, "repository", FakeRepository())

        login_response = local_client.post(
            "/platform/auth/session",
            json={"admin_token": "viewer-cookie-token", "admin_actor": "cookie-user"},
        )
        assert login_response.status_code == 200

        summary_response = local_client.get("/admin/observability/summary")

        assert summary_response.status_code == 200
        assert summary_response.json()["totals"]["messages"] == 1


def test_readiness_check() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database_ok"] is True
    assert response.json()["blocker_count"] == 0


def test_readiness_check_returns_503_when_runtime_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "probe ok"},
    )
    monkeypatch.setattr(
        main_module,
        "get_system_readiness",
        lambda: {
            "overall_status": "blocked",
            "pilot_ready": False,
            "scale_ready": False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "get_runtime_blockers",
        lambda: [{"code": "postgres_target", "detail": "runtime still on sqlite"}],
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["blockers"] == ["postgres_target"]


def test_incoming_message(monkeypatch) -> None:
    monkeypatch.setattr(routes, "process_incoming_message", lambda **kwargs: {
        "contact_id": kwargs["contact_id"],
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.91,
        "reply": "Olá, posso te ajudar com isso.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "qualificando",
        "next_action": "Entender escopo.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "oi",
    }
    response = client.post("/messages/incoming", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Olá, posso te ajudar com isso."
    assert data["source"] == "openai"
    assert response.headers["X-Request-ID"]


def test_incoming_message_empty_after_trim() -> None:
    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "   ",
    }
    response = client.post("/messages/incoming", json=payload)

    assert response.status_code == 400


def test_incoming_message_rate_limited(monkeypatch) -> None:
    clear_rate_limit_counters()
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_incoming_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(routes, "process_incoming_message", lambda **kwargs: {
        "contact_id": kwargs["contact_id"],
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.91,
        "reply": "Olá, posso te ajudar com isso.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "qualificando",
        "next_action": "Entender escopo.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "oi",
    }
    headers = {"X-Forwarded-For": "198.51.100.10"}
    first_response = client.post("/messages/incoming", json=payload, headers=headers)
    second_response = client.post("/messages/incoming", json=payload, headers=headers)

    assert first_response.status_code == 200
    assert first_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.status_code == 429
    assert second_response.json()["detail"] == "Too many requests."
    assert second_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.headers["Retry-After"]
    clear_rate_limit_counters()


def test_whatsapp_webhook(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.services import inbound_service
    from app.storage.inbound_repository import list_jobs
    company_id = company_factory()
    enable_normalized_ingress(monkeypatch)
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("HTTP must only enqueue"))
    message_id = uuid4().hex
    response = client.post("/webhooks/whatsapp", params={"company_id": company_id},
        json={"event": "message.received", "from_number": "5511999999999", "text": "Olá", "provider_message_id": message_id},
        headers=NORMALIZED_INGRESS_HEADERS)
    assert response.status_code == 200
    assert response.json()["queued_count"] == 1
    assert response.json()["processed"] is False
    assert list_jobs(company_id)[0]["status"] == "pending"
    assert response.headers["X-Request-ID"]


def test_whatsapp_webhook_accepts_meta_payload_and_queues_auto_reply(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.storage.inbound_repository import list_jobs
    cid = company_factory()
    monkeypatch.setattr(settings, "whatsapp_app_secret", "test-secret")
    monkeypatch.setattr(routes, "_resolve_meta_company", lambda _: {"company_id": cid, "company_slug": None})
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "bound"}, "messages": [{"id": uuid4().hex, "from": "5511999999999", "type": "text", "text": {"body": "Olá"}}]
    }}]}]}
    raw = json.dumps(payload).encode()
    signature = hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": "sha256="+signature})
    assert response.status_code == 200
    assert response.json()["payload_mode"] == "meta"
    assert list_jobs(cid)[0]["outbound_message_id"] is None


def test_whatsapp_webhook_ignores_duplicate_provider_message_id(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.storage.inbound_repository import list_jobs
    cid = company_factory()
    enable_normalized_ingress(monkeypatch)
    payload = {"event": "message.received", "from_number": "5511999999999", "text": "Olá", "provider_message_id": uuid4().hex}
    first = client.post("/webhooks/whatsapp", json=payload, params={"company_id": cid}, headers=NORMALIZED_INGRESS_HEADERS)
    second = client.post("/webhooks/whatsapp", json=payload, params={"company_id": cid}, headers=NORMALIZED_INGRESS_HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["queued_count"] == 1
    assert second.json()["queued_count"] == 0
    assert second.json()["jobs"][0]["duplicate"] is True
    assert len(list_jobs(cid)) == 1


def test_whatsapp_webhook_ignores_non_message_meta_events(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    raw_payload = (
        b'{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"statuses":'
        b'[{"id":"wamid.status.1","status":"delivered"}]}}]}]}'
    )
    signature = hmac.new(b"meta-app-secret", msg=raw_payload, digestmod=hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/whatsapp",
        content=raw_payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False


def test_whatsapp_webhook_rejects_unsigned_meta_status_event(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.status.1", "status": "delivered"}]}}]}],
    }

    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 403


def test_whatsapp_webhook_rejects_invalid_meta_signature(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "5511999999999",
                                    "profile": {"name": "Cliente Meta"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid.inbound.123",
                                    "from": "5511999999999",
                                    "type": "text",
                                    "text": {"body": "Oi, gostaria de agendar"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    response = client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )

    assert response.status_code == 403


def test_verify_whatsapp_webhook(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "verify-me")

    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 200
    assert response.text == "123456"


def test_verify_whatsapp_webhook_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "verify-me")

    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 403
