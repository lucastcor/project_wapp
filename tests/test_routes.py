from fastapi.testclient import TestClient

from app.main import app
from app.api import routes

client = TestClient(app)


def test_healthcheck() -> None:
    response = client.get("/health", headers={"X-Request-ID": "req-health-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "req-health-123"


def test_readiness_check() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database_ok"] is True


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


def test_whatsapp_webhook(monkeypatch) -> None:
    monkeypatch.setattr(routes, "process_whatsapp_webhook", lambda **kwargs: {
        "contact_id": kwargs["from_number"],
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
    }
    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["contact_id"] == "5511999999999"
    assert data["reply"] == "Recebi sua mensagem."
    assert response.headers["X-Request-ID"]
