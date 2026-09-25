"""Mensagem que exige humano: aviso ao cliente por padrão, silêncio só por política.

Contrato P01 (docs/piloto/CONTRATOS.md): o silêncio deixa de ser o
comportamento implícito de um handoff — o cliente não saberia que alguém vai
responder. Continua possível por escolha explícita da empresa
(`conversation.handoff_notice_enabled = false`), e esse caminho segue coberto.
"""

from app.core import orchestrator


class FakeRepository:
    def __init__(self) -> None:
        self.saved_contact = None
        self.saved_message = None
        self.updated_memory = None
        self.pending_questions: list[dict] = []
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
        self.pending_questions.append(kwargs)
        return len(self.pending_questions)

    def mark_clarification_consumed(self, question_id: int, *, company_id: int | None = None) -> None:
        raise AssertionError("Clarification should not be consumed in this flow.")

    def get_latest_message_with_reply_source(self, contact_id: str, *, company_id: int, reply_source: str):
        return None

    def save_message(self, **kwargs) -> None:
        self.saved_message = kwargs

    def update_contact_memory(self, **kwargs) -> None:
        self.updated_memory = kwargs


def _run_financial_message(monkeypatch, runtime) -> tuple[dict, FakeRepository]:
    fake_repository = FakeRepository()
    monkeypatch.setattr(orchestrator, "repository", fake_repository)
    monkeypatch.setattr(orchestrator, "resolve_company_runtime", runtime)
    monkeypatch.setattr(orchestrator, "emit_handoff_created", lambda **kwargs: None)
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
        "generate_business_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Handoff must not generate an LLM reply.")),
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
    return result, fake_repository


def test_handoff_intent_notifies_customer_and_creates_pending_question(
    monkeypatch, global_company_runtime
) -> None:
    result, fake_repository = _run_financial_message(monkeypatch, global_company_runtime())

    assert result["classification"] == "financeiro"
    assert result["action"] == "handoff_human"
    assert result["auto_reply"] is True
    assert result["source"] == "policy_handoff_notice"
    assert "encaminhei para a nossa equipe" in result["reply"]
    assert result["handoff_required"] is True
    assert result["question_type"] == "handoff_required"
    assert len(fake_repository.pending_questions) == 1
    assert result["pipeline_stage"] == "encerrado"
    assert result["company_id"] == 1
    assert result["company_slug"] == "default"


def test_process_contact_message_without_auto_reply(monkeypatch, global_company_runtime) -> None:
    runtime = global_company_runtime(policies={"conversation": {"handoff_notice_enabled": False}})
    result, fake_repository = _run_financial_message(monkeypatch, runtime)

    assert result["classification"] == "financeiro"
    assert result["reply"] == ""
    assert result["source"] == "none"
    assert result["auto_reply"] is False
    assert result["action"] == "handoff_human"
    # Silêncio para o cliente não significa ninguém saber: a pendência existe.
    assert result["handoff_required"] is True
    assert len(fake_repository.pending_questions) == 1
    assert result["pipeline_stage"] == "encerrado"
    assert result["company_id"] == 1
    assert result["company_slug"] == "default"
