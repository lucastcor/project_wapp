import hashlib
import hmac

from fastapi.testclient import TestClient

from app import main as main_module
from app.api import routes
from app.core.config import settings
from app.main import app
from app.services.rate_limit_service import reset_rate_limit_state

client = TestClient(app)


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

        # A rota-sonda é catálogo estático de propósito. O que este teste prova
        # é que o cookie de sessão autentica; usar `/admin/observability/summary`
        # amarrava a prova de autenticação à política daquela rota, e quando o
        # T-07 a moveu para `platform_global` o teste quebrou por um motivo que
        # não tem nada a ver com sessão.
        taxonomy_response = local_client.get("/admin/taxonomy")
        assert taxonomy_response.status_code == 200

        # E o mesmo cookie de viewer não alcança postura da plataforma.
        summary_response = local_client.get("/admin/observability/summary")
        assert summary_response.status_code == 403


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


def test_incoming_message(monkeypatch, tenant_credential_factory) -> None:
    credential = tenant_credential_factory()
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
    response = client.post(
        "/messages/incoming",
        json=payload,
        headers={"X-Tenant-Token": credential["token"]},
    )

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


def test_incoming_message_rate_limited(monkeypatch, tenant_credential_factory) -> None:
    credential = tenant_credential_factory()
    reset_rate_limit_state()
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_incoming_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
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
    headers = {
        "X-Forwarded-For": "198.51.100.10",
        "X-Tenant-Token": credential["token"],
    }
    first_response = client.post("/messages/incoming", json=payload, headers=headers)
    second_response = client.post("/messages/incoming", json=payload, headers=headers)

    assert first_response.status_code == 200
    assert first_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.status_code == 429
    assert second_response.json()["detail"] == "Too many requests."
    assert second_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.headers["Retry-After"]
    reset_rate_limit_state()


def test_whatsapp_webhook(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.receipts_started: list[dict] = []
            self.completed_receipts: list[dict] = []
            self.failed_receipts: list[dict] = []

        def begin_inbound_message_processing(self, **kwargs):
            self.receipts_started.append(kwargs)
            return "started"

        def mark_inbound_message_processing_completed(self, **kwargs):
            self.completed_receipts.append(kwargs)

        def mark_inbound_message_processing_failed(self, **kwargs):
            self.failed_receipts.append(kwargs)

        def create_outbound_message(self, **kwargs):
            return 123

    fake_repository = FakeRepository()
    monkeypatch.setattr(routes, "repository", fake_repository)
    monkeypatch.setattr(routes, "dispatch_outbound_message", lambda outbound_message_id, channel=None, company_id=None: {
        "outbound_message_id": outbound_message_id,
        "delivery_status": "sent",
        "provider_message_id": "wamid.test.123",
        "provider_response": {"messages": [{"id": "wamid.test.123"}]},
    })
    monkeypatch.setattr(routes, "process_whatsapp_webhook", lambda **kwargs: {
        "contact_id": kwargs["from_number"],
        "company_id": 1,
        "company_slug": "default",
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.91,
        "reply": "Recebi sua mensagem.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "novo_lead",
        "next_action": "Entender necessidade.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    payload = {
        "event": "message.received",
        "from_number": "5511999999999",
        "from_name": "Cliente Teste",
        "text": "Olá",
        "provider_message_id": "wamid.normalized.123",
    }
    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["contact_id"] == "5511999999999"
    assert data["reply"] == "Recebi sua mensagem."
    assert data["provider_message_id"] == "wamid.normalized.123"
    assert data["auto_reply_dispatch"]["delivery_status"] == "sent"
    assert fake_repository.receipts_started[0]["provider_message_id"] == "wamid.normalized.123"
    assert fake_repository.completed_receipts == [
        {
            "provider": "whatsapp",
            "provider_message_id": "wamid.normalized.123",
            "company_id": 1,
        }
    ]
    assert fake_repository.failed_receipts == []
    assert response.headers["X-Request-ID"]


def test_whatsapp_webhook_accepts_meta_payload_and_dispatches_auto_reply(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.created_messages: list[dict] = []
            self.receipts_started: list[dict] = []
            self.completed_receipts: list[dict] = []

        def begin_inbound_message_processing(self, **kwargs):
            self.receipts_started.append(kwargs)
            return "started"

        def mark_inbound_message_processing_completed(self, **kwargs):
            self.completed_receipts.append(kwargs)

        def mark_inbound_message_processing_failed(self, **kwargs):
            raise AssertionError("Receipt should not be marked as failed on successful processing.")

        def create_outbound_message(self, **kwargs):
            self.created_messages.append(kwargs)
            return 321

    fake_repository = FakeRepository()
    monkeypatch.setattr(routes, "repository", fake_repository)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    monkeypatch.setattr(routes, "dispatch_outbound_message", lambda outbound_message_id, channel=None, company_id=None: {
        "outbound_message_id": outbound_message_id,
        "delivery_status": "sent",
        "provider_message_id": "wamid.test.999",
        "provider_response": {"messages": [{"id": "wamid.test.999"}]},
    })
    monkeypatch.setattr(routes, "process_whatsapp_webhook", lambda **kwargs: {
        "contact_id": kwargs["from_number"],
        "company_id": 1,
        "company_slug": "default",
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.93,
        "reply": "Claro, posso te ajudar.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "novo_lead",
        "next_action": "Qualificar demanda.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    raw_payload = (
        b'{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"contacts":[{"wa_id":"5511999999999","profile":{"name":"Cliente Meta"}}],"messages":[{"id":"wamid.inbound.123","from":"5511999999999","type":"text","text":{"body":"Oi, gostaria de agendar"}}]}}]}]}'
    )
    signature = hmac.new(
        b"meta-app-secret",
        msg=raw_payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    response = client.post(
        "/webhooks/whatsapp",
        content=raw_payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["payload_mode"] == "meta"
    assert data["provider_message_id"] == "wamid.inbound.123"
    assert data["auto_reply_dispatch"]["delivery_status"] == "sent"
    assert fake_repository.created_messages[0]["source"] == "whatsapp_auto_reply"
    assert fake_repository.created_messages[0]["contact_id"] == "5511999999999"
    assert fake_repository.receipts_started[0]["provider_message_id"] == "wamid.inbound.123"
    assert fake_repository.completed_receipts == [
        {
            "provider": "whatsapp",
            "provider_message_id": "wamid.inbound.123",
            "company_id": 1,
        }
    ]


def test_whatsapp_webhook_ignores_duplicate_provider_message_id(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.receipts_started: list[dict] = []

        def begin_inbound_message_processing(self, **kwargs):
            self.receipts_started.append(kwargs)
            return "completed"

        def mark_inbound_message_processing_completed(self, **kwargs):
            raise AssertionError("Duplicate payload should not be marked as completed again.")

        def mark_inbound_message_processing_failed(self, **kwargs):
            raise AssertionError("Duplicate payload should not be marked as failed.")

        def create_outbound_message(self, **kwargs):
            raise AssertionError("Duplicate payload should not enqueue outbound messages.")

    fake_repository = FakeRepository()
    monkeypatch.setattr(routes, "repository", fake_repository)
    monkeypatch.setattr(
        routes,
        "process_whatsapp_webhook",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Duplicate payload should not be processed.")),
    )

    payload = {
        "event": "message.received",
        "from_number": "5511999999999",
        "from_name": "Cliente Teste",
        "text": "Olá",
        "provider_message_id": "wamid.normalized.duplicate",
    }
    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "processed": False,
        "reason": "duplicate_provider_message_id",
        "duplicate_status": "completed",
        "payload_mode": "normalized",
        "provider_message_id": "wamid.normalized.duplicate",
    }
    assert fake_repository.receipts_started[0]["provider_message_id"] == "wamid.normalized.duplicate"


def test_whatsapp_webhook_ignores_non_message_meta_events() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.status.1",
                                    "status": "delivered",
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }
    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 200
    assert response.json()["processed"] is False


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
