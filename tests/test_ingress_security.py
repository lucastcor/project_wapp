"""Authentication of the public inbound message entry points."""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings
from app.main import app
from app.services import ingress_auth_service

client = TestClient(app)

INTERNAL_SECRET = "unit-test-internal-ingress-secret"
META_APP_SECRET = "unit-test-meta-app-secret"
META_PHONE_NUMBER_ID = "111222333444555"

NORMALIZED_PAYLOAD = {
    "event": "message.received",
    "from_number": "5511999999999",
    "from_name": "Cliente Teste",
    "text": "Olá",
}

ORCHESTRATOR_RESULT = {
    "contact_id": "5511999999999",
    "company_id": 1,
    "company_slug": "default",
    "classification": "lead_novo",
    "classification_reason": "Interesse comercial claro.",
    "classification_confidence": 0.9,
    "reply": "Recebi sua mensagem.",
    "source": "template",
    "auto_reply": False,
    "action": "handoff_human",
    "priority": "high",
    "pipeline_stage": "novo_lead",
    "next_action": "Entender necessidade.",
    "lead_temperature": "warm",
    "handoff_required": False,
    "pending_question_id": None,
    "question_type": None,
}


class _StubRepository:
    """Repository stub that records receipts and never touches the database."""

    def __init__(self) -> None:
        self.started: list[dict] = []

    def begin_inbound_message_processing(self, **kwargs) -> str:
        self.started.append(kwargs)
        return "started"

    def mark_inbound_message_processing_completed(self, **kwargs) -> None:
        return None

    def mark_inbound_message_processing_failed(self, **kwargs) -> None:
        return None

    def create_outbound_message(self, **kwargs) -> int:
        return 1

    def get_company(self, company_id: int):
        return {"id": 1, "slug": "default"} if company_id == 1 else None

    def get_company_by_slug(self, slug: str):
        return None

    def get_whatsapp_binding_by_phone_number_id(
        self,
        phone_number_id: str,
        *,
        active_only: bool = True,
    ):
        if phone_number_id != META_PHONE_NUMBER_ID:
            return None
        return {
            "id": 1,
            "company_id": 1,
            "company_slug": "tenant-a",
            "company_active": 1,
            "phone_number_id": phone_number_id,
        }


@pytest.fixture
def stub_pipeline(monkeypatch) -> _StubRepository:
    repository = _StubRepository()
    monkeypatch.setattr(routes, "repository", repository)
    def enqueue(items):
        repository.started.extend(items)
        return [{"job_id": i + 1, "status": "pending", "duplicate": False} for i, _ in enumerate(items)]
    monkeypatch.setattr(routes, "enqueue_batch", enqueue)
    monkeypatch.setattr(routes, "process_incoming_message", lambda **kwargs: dict(ORCHESTRATOR_RESULT))
    return repository


def _sign(raw_body: bytes, secret: str = META_APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()


def _meta_body(phone_number_id: str = META_PHONE_NUMBER_ID) -> bytes:
    return (
        '{"object":"whatsapp_business_account","entry":[{"changes":[{"value":'
        '{"metadata":{"display_phone_number":"5511888887777","phone_number_id":"'
        + phone_number_id
        + '"},'
        '"contacts":[{"wa_id":"5511999999999","profile":{"name":"Cliente Meta"}}],'
        '"messages":[{"id":"wamid.meta.1","from":"5511999999999","type":"text",'
        '"text":{"body":"Oi"}}]}}]}]}'
    ).encode("utf-8")


# ── Meta signature ────────────────────────────────────────────────────


def test_meta_payload_without_signature_is_rejected(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", META_APP_SECRET)

    response = client.post(
        "/webhooks/whatsapp",
        content=_meta_body(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 403
    assert stub_pipeline.started == []


def test_meta_payload_with_invalid_signature_is_rejected(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", META_APP_SECRET)

    response = client.post(
        "/webhooks/whatsapp",
        content=_meta_body(),
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(_meta_body(), "wrong-secret"),
        },
    )

    assert response.status_code == 403
    assert stub_pipeline.started == []


def test_meta_payload_with_valid_signature_is_accepted(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", META_APP_SECRET)
    raw_body = _meta_body()

    response = client.post(
        "/webhooks/whatsapp",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw_body),
        },
    )

    assert response.status_code == 200
    assert response.json()["payload_mode"] == "meta"
    assert stub_pipeline.started[0]["provider_message_id"] == "wamid.meta.1"


def test_meta_ingress_does_not_depend_on_internal_secret(monkeypatch, stub_pipeline) -> None:
    """The Meta path stays open with the normalized ingress disabled."""
    monkeypatch.setattr(settings, "whatsapp_app_secret", META_APP_SECRET)
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", False)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)
    raw_body = _meta_body()

    response = client.post(
        "/webhooks/whatsapp",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(raw_body)},
    )

    assert response.status_code == 200


# ── Normalized ingress ────────────────────────────────────────────────


def test_normalized_ingress_is_disabled_by_default(stub_pipeline) -> None:
    assert settings.enable_normalized_whatsapp_ingress is False

    response = client.post("/webhooks/whatsapp", json=NORMALIZED_PAYLOAD)

    assert response.status_code == 403
    assert response.json()["detail"] == "Normalized webhook ingress is disabled."
    assert stub_pipeline.started == []


def test_normalized_ingress_enabled_without_secret_fails_closed(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    response = client.post("/webhooks/whatsapp", json=NORMALIZED_PAYLOAD)

    assert response.status_code == 503
    assert stub_pipeline.started == []


def test_normalized_ingress_with_placeholder_secret_fails_closed(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", "change-me-internal-ingress-secret")

    response = client.post("/webhooks/whatsapp", json=NORMALIZED_PAYLOAD)

    assert response.status_code == 503
    assert stub_pipeline.started == []


def test_normalized_ingress_without_credentials_is_rejected(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post("/webhooks/whatsapp", json=NORMALIZED_PAYLOAD)

    assert response.status_code == 401
    assert stub_pipeline.started == []


def test_normalized_ingress_with_invalid_credentials_is_rejected(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/webhooks/whatsapp",
        json=NORMALIZED_PAYLOAD,
        headers={"X-Internal-Ingress-Secret": "not-the-secret"},
    )

    assert response.status_code == 401
    assert stub_pipeline.started == []


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Internal-Ingress-Secret": INTERNAL_SECRET},
        {"Authorization": f"Bearer {INTERNAL_SECRET}"},
    ],
    ids=["header", "bearer"],
)
def test_normalized_ingress_with_valid_credentials_is_accepted(monkeypatch, stub_pipeline, headers) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/webhooks/whatsapp",
        json={**NORMALIZED_PAYLOAD, "provider_message_id": "wamid.normalized.ok"},
        params={"company_id": 1},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["payload_mode"] == "normalized"


def test_ingress_failure_never_logs_the_secret(monkeypatch, stub_pipeline, caplog) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    with caplog.at_level("DEBUG"):
        response = client.post(
            "/webhooks/whatsapp",
            json=NORMALIZED_PAYLOAD,
            headers={"X-Internal-Ingress-Secret": "leaked-attempt-secret"},
        )

    assert response.status_code == 401
    log_text = caplog.text
    assert INTERNAL_SECRET not in log_text
    assert "leaked-attempt-secret" not in log_text


# ── /messages/incoming ────────────────────────────────────────────────


def test_incoming_message_is_open_in_development(stub_pipeline) -> None:
    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 200


def test_incoming_message_rejects_anonymous_calls_in_production(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 401


def test_incoming_message_rejects_invalid_secret_in_production(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
        headers={"X-Internal-Ingress-Secret": "wrong"},
    )

    assert response.status_code == 401


def test_incoming_message_accepts_valid_secret_in_production(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
        headers={"X-Internal-Ingress-Secret": INTERNAL_SECRET},
    )

    assert response.status_code == 200


def test_incoming_message_fails_closed_when_production_secret_is_missing(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
        headers={"X-Internal-Ingress-Secret": INTERNAL_SECRET},
    )

    assert response.status_code == 503


def test_incoming_message_rejects_credentials_when_none_is_configured(stub_pipeline) -> None:
    """A credential is never accepted without a configured secret to verify it."""
    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
        headers={"X-Internal-Ingress-Secret": "some-guess"},
    )

    assert response.status_code == 401


def test_strict_runtime_validation_also_enforces_ingress(monkeypatch, stub_pipeline) -> None:
    monkeypatch.setattr(settings, "strict_runtime_validation", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    assert ingress_auth_service.internal_ingress_enforced() is True

    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 401


# ── Tenant authorization and payload limits ───────────────────────────


def test_incoming_message_rejects_unknown_company_slug(stub_pipeline) -> None:
    response = client.post(
        "/messages/incoming",
        params={"company_slug": "does-not-exist"},
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found."


def test_incoming_message_rejects_unknown_company_id(stub_pipeline) -> None:
    response = client.post(
        "/messages/incoming",
        params={"company_id": 987654321},
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 404


def test_incoming_message_rejects_non_numeric_company_header(stub_pipeline) -> None:
    response = client.post(
        "/messages/incoming",
        json={"contact_id": "5511999999999", "message": "oi"},
        headers={"X-Company-Id": "not-an-int"},
    )

    assert response.status_code == 422


def test_incoming_message_resolves_an_existing_company(stub_pipeline, monkeypatch) -> None:
    captured: dict = {}

    class _CompanyRepository(_StubRepository):
        def get_company_by_slug(self, slug: str):
            return {"id": 42, "slug": slug}

    monkeypatch.setattr(routes, "repository", _CompanyRepository())

    def _capture(**kwargs):
        captured.update(kwargs)
        return dict(ORCHESTRATOR_RESULT)

    monkeypatch.setattr(routes, "process_incoming_message", _capture)

    response = client.post(
        "/messages/incoming",
        params={"company_slug": "tenant-a"},
        json={"contact_id": "5511999999999", "message": "oi"},
    )

    assert response.status_code == 200
    assert captured["company_id"] == 42
    assert captured["company_slug"] == "tenant-a"


@pytest.mark.parametrize(
    "payload",
    [
        {"contact_id": "x" * 65, "message": "oi"},
        {"contact_id": "5511999999999", "message": "x" * 4097},
        {"contact_id": "5511999999999", "contact_name": "n" * 201, "message": "oi"},
    ],
    ids=["contact_id", "message", "contact_name"],
)
def test_incoming_message_enforces_field_size_limits(stub_pipeline, payload) -> None:
    response = client.post("/messages/incoming", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "x" * 4097},
        {"from_number": "5" * 65},
        {"from_name": "n" * 201},
        {"event": "e" * 101},
    ],
    ids=["text", "from_number", "from_name", "event"],
)
def test_normalized_webhook_enforces_field_size_limits(monkeypatch, stub_pipeline, overrides) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", INTERNAL_SECRET)

    response = client.post(
        "/webhooks/whatsapp",
        json={**NORMALIZED_PAYLOAD, **overrides},
        headers={"X-Internal-Ingress-Secret": INTERNAL_SECRET},
    )

    assert response.status_code == 422
    assert stub_pipeline.started == []
