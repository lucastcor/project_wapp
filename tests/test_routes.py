from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthcheck() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_message() -> None:
    payload = {"message": "oi"}
    response = client.post("/chat/message", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Resposta simulada para: oi"
    assert data["source"] == "mock-llm"


def test_chat_message_empty() -> None:
    payload = {"message": "   "}
    response = client.post("/chat/message", json=payload)

    assert response.status_code == 400