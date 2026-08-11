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
            "conversation_summary": "Contato já demonstrou interesse inicial.",
            "last_classification": "lead_novo",
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


def test_process_contact_message_full_flow(monkeypatch, stub_company_config) -> None:
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
    assert result["company_id"] == 1
    assert result["company_slug"] == "default"

    assert fake_repository.saved_contact is not None
    assert fake_repository.saved_message is not None
    assert fake_repository.updated_memory is not None


def test_process_contact_message_uses_workflow_when_handled(monkeypatch, stub_company_config) -> None:
    fake_repository = FakeRepository()
    monkeypatch.setattr(orchestrator, "repository", fake_repository)
    stub_company_config(orchestrator, company_id=1, company_slug="default", company_name="Default Company")
    monkeypatch.setattr(
        orchestrator,
        "handle_conversation_workflow",
        lambda **kwargs: type(
            "WorkflowStub",
            (),
            {
                "handled": True,
                "classification": "agendamento",
                "reply": "Tenho um horário livre amanhã às 10h. Posso confirmar?",
                "source": "workflow",
                "action": "schedule_confirmation_requested",
                "priority": "high",
                "auto_reply": True,
                "workflow_type": "scheduling",
            },
        )(),
    )
    monkeypatch.setattr(
        orchestrator,
        "update_conversation_summary",
        lambda **kwargs: "Resumo com fluxo de agendamento.",
    )
    monkeypatch.setattr(
        orchestrator,
        "infer_pipeline_state",
        lambda **kwargs: {
            "pipeline_stage": "aguardando_confirmacao",
            "next_action": "Confirmar o agendamento com o cliente.",
            "lead_temperature": "hot",
        },
    )

    result = orchestrator.process_incoming_message(
        contact_id="5511999999999",
        contact_name="Cliente Teste",
        message="Quero agendar amanhã às 10h",
    )

    assert result["classification"] == "agendamento"
    assert result["source"] == "workflow"
    assert result["action"] == "schedule_confirmation_requested"
    assert result["handoff_required"] is False
    assert fake_repository.saved_message["classification"] == "agendamento"
