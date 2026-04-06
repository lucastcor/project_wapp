from app.core import orchestrator


class FakeRepository:
    def __init__(self) -> None:
        self.saved_contacts: list[dict] = []
        self.saved_messages: list[dict] = []
        self.default_company_id = 7

    def resolve_company_id(
        self,
        company_id: int | None = None,
        company_slug: str | None = None,
    ) -> int | None:
        if company_slug == "empresa-teste":
            return self.default_company_id
        return company_id

    def ensure_default_company(self) -> int:
        return self.default_company_id

    def get_company(self, company_id: int) -> dict:
        return {
            "id": company_id,
            "slug": "empresa-teste",
            "name": "Empresa Teste",
        }

    def save_contact(
        self,
        contact_id: str,
        contact_name: str | None,
        company_id: int | None = None,
    ) -> None:
        self.saved_contacts.append(
            {
                "contact_id": contact_id,
                "contact_name": contact_name,
                "company_id": company_id,
            }
        )

    def get_contact(self, contact_id: str) -> dict | None:
        return None

    def get_latest_customer_clarification(self, contact_id: str) -> None:
        return None

    def save_message(self, **kwargs) -> None:
        self.saved_messages.append(kwargs)

    def create_pending_question(self, **kwargs) -> int:
        raise AssertionError("Pending question should not be created in this scenario.")

    def mark_clarification_consumed(self, question_id: int) -> None:
        raise AssertionError("Clarification should not be consumed in this scenario.")

    def update_contact_memory(self, **kwargs) -> None:
        return None


def test_process_incoming_message_propagates_company_scope(monkeypatch) -> None:
    fake_repository = FakeRepository()
    captured_reply_kwargs: dict = {}

    monkeypatch.setattr(orchestrator, "repository", fake_repository)
    monkeypatch.setattr(
        orchestrator,
        "handle_conversation_workflow",
        lambda **kwargs: type("WorkflowStub", (), {"handled": False})(),
    )
    monkeypatch.setattr(
        orchestrator,
        "classify_message",
        lambda message: {
            "classification": "lead_novo",
            "confidence": 0.93,
            "reason": "Mensagem comercial.",
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

    def fake_generate_business_reply(**kwargs):
        captured_reply_kwargs.update(kwargs)
        return {
            "reply": "Resposta da empresa correta.",
            "source": "openai",
        }

    monkeypatch.setattr(orchestrator, "generate_business_reply", fake_generate_business_reply)
    monkeypatch.setattr(
        orchestrator,
        "update_conversation_summary",
        lambda **kwargs: "Resumo atualizado.",
    )
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {
            "pipeline_stage": "novo_lead",
            "next_action": "Qualificar contato.",
            "lead_temperature": "warm",
        },
    )

    result = orchestrator.process_incoming_message(
        contact_id="5511999991234",
        contact_name="Cliente Teste",
        message="Quero saber mais sobre o serviço.",
        company_slug="empresa-teste",
    )

    assert result["company_id"] == 7
    assert result["company_slug"] == "empresa-teste"
    assert captured_reply_kwargs["company_id"] == 7
    assert captured_reply_kwargs["company_slug"] == "empresa-teste"
    assert fake_repository.saved_contacts[0]["company_id"] == 7
    assert fake_repository.saved_messages[0]["company_id"] == 7
