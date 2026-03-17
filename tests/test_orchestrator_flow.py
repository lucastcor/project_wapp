from app.core import orchestrator


class FakeRepository:
    def __init__(self) -> None:
        self.saved_contact = None
        self.saved_message = None
        self.updated_memory = None

    def save_contact(self, contact_id: str, contact_name: str | None) -> None:
        self.saved_contact = {
            "contact_id": contact_id,
            "contact_name": contact_name,
        }

    def get_contact(self, contact_id: str) -> dict:
        return {
            "contact_id": contact_id,
            "contact_name": "Cliente Teste",
            "conversation_summary": "Contato já demonstrou interesse inicial.",
            "last_classification": "lead_novo",
        }

    def save_message(self, **kwargs) -> None:
        self.saved_message = kwargs

    def update_contact_memory(self, **kwargs) -> None:
        self.updated_memory = kwargs


def test_process_contact_message_full_flow(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(orchestrator, "repository", fake_repository)

    monkeypatch.setattr(
        orchestrator,
        "classify_message",
        lambda message: {
            "classification": "lead_novo",
            "confidence": 0.91,
            "reason": "Interesse comercial claro.",
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "decide_message_action",
        lambda classification, confidence: {
            "auto_reply": True,
            "action": "auto_reply",
            "priority": "high",
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "generate_business_reply",
        lambda **kwargs: {
            "reply": "Olá, posso te explicar como funciona.",
            "source": "openai",
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "update_conversation_summary",
        lambda **kwargs: "Resumo consolidado do contato.",
    )

    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {
            "pipeline_stage": "qualificando",
            "next_action": "Entender escopo e alinhar proposta.",
            "lead_temperature": "warm",
        },
    )

    result = orchestrator.process_incoming_message(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="Quero entender melhor o serviço e os valores.",
    )

    assert result["classification"] == "lead_novo"
    assert result["reply"] == "Olá, posso te explicar como funciona."
    assert result["source"] == "openai"
    assert result["pipeline_stage"] == "qualificando"
    assert result["lead_temperature"] == "warm"

    assert fake_repository.saved_contact is not None
    assert fake_repository.saved_message is not None
    assert fake_repository.updated_memory is not None