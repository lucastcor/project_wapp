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
            "conversation_summary": "Contato fez pergunta anterior.",
            "last_classification": "financeiro",
        }

    def save_message(self, **kwargs) -> None:
        self.saved_message = kwargs

    def update_contact_memory(self, **kwargs) -> None:
        self.updated_memory = kwargs


def test_process_contact_message_without_auto_reply(monkeypatch) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(orchestrator, "repository", fake_repository)

    monkeypatch.setattr(
        orchestrator,
        "classify_message",
        lambda message: {
            "classification": "financeiro",
            "confidence": 0.92,
            "reason": "Mensagem sobre pagamento pendente.",
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "decide_message_action",
        lambda classification, confidence: {
            "auto_reply": False,
            "action": "handoff_human",
            "priority": "high",
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "update_conversation_summary",
        lambda **kwargs: "Resumo atualizado sem resposta automática.",
    )

    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {
            "pipeline_stage": "encerrado",
            "next_action": "Encaminhar para atendimento humano.",
            "lead_temperature": "cold",
        },
    )

    result = orchestrator.process_incoming_message(
        contact_id="5511888888888",
        contact_name="Financeiro Teste",
        message="Preciso tratar um pagamento pendente.",
    )

    assert result["classification"] == "financeiro"
    assert result["reply"] == ""
    assert result["source"] == "none"
    assert result["auto_reply"] is False
    assert result["action"] == "handoff_human"
    assert result["pipeline_stage"] == "encerrado"