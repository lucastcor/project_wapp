from app.core import orchestrator


class FakeRepository:
    def __init__(self) -> None:
        self.saved_contact = None
        self.saved_message = None
        self.updated_memory = None
        self.default_company_id = 1

    def resolve_company_id(
        self,
        company_id: int | None = None,
        company_slug: str | None = None,
    ) -> int | None:
        return company_id

    def ensure_default_company(self) -> int:
        return self.default_company_id

    def get_company(self, company_id: int) -> dict:
        return {
            "id": company_id,
            "slug": "default",
            "name": "Default Company",
        }

    def save_contact(
        self,
        contact_id: str,
        contact_name: str | None,
        company_id: int | None = None,
    ) -> None:
        self.saved_contact = {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "company_id": company_id,
        }

    def get_contact(self, contact_id: str, *, company_id: int | None = None) -> dict:
        return {
            "contact_id": contact_id,
            "contact_name": "Cliente Teste",
            "company_id": company_id if company_id is not None else self.default_company_id,
            "conversation_summary": "Contato fez pergunta anterior.",
            "last_classification": "financeiro",
        }

    def get_latest_customer_clarification(
        self,
        contact_id: str,
        *,
        company_id: int | None = None,
    ) -> None:
        return None

    def create_pending_question(self, **kwargs) -> int:
        raise AssertionError("Pending question should not be created in this flow.")

    def mark_clarification_consumed(self, question_id: int, *, company_id: int | None = None) -> None:
        raise AssertionError("Clarification should not be consumed in this flow.")

    def save_message(self, **kwargs) -> None:
        self.saved_message = kwargs

    def update_contact_memory(self, **kwargs) -> None:
        self.updated_memory = kwargs


def test_process_contact_message_without_auto_reply(monkeypatch, stub_company_config) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(orchestrator, "repository", fake_repository)
    stub_company_config(orchestrator, company_id=1, company_slug="default", company_name="Default Company")
    monkeypatch.setattr(
        orchestrator,
        "handle_conversation_workflow",
        lambda **kwargs: type("WorkflowStub", (), {"handled": False})(),
    )

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
    assert result["company_id"] == 1
    assert result["company_slug"] == "default"
