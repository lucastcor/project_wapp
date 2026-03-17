from app.core.config import settings
from app.services.reply_services import generate_business_reply
from app.services import reply_services


def test_reply_service_fallback_for_lead(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = generate_business_reply(
        classification="lead_novo",
        message="Quero saber mais sobre o serviço.",
        contact_name="Ana",
        conversation_summary="Contato iniciou conversa.",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
    )

    assert result["source"] == "template"
    assert "Ana" in result["reply"] or "Olá" in result["reply"]


def test_reply_service_fallback_for_non_auto_reply_case(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = generate_business_reply(
        classification="financeiro",
        message="Tenho dúvida sobre pagamento.",
        contact_name="Marina",
        conversation_summary="Contato mencionou nota fiscal.",
        last_classification="financeiro",
        pipeline_stage="encerrado",
    )

    assert result["source"] == "none"
    assert result["reply"] == ""


def test_reply_service_returns_openai_source_when_finetuned_flag_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "use_finetuned_model_for_replies", False)
    monkeypatch.setattr(settings, "openai_finetuned_model", None)

    class FakeProvider:
        def generate_business_reply(self, **kwargs) -> str:
            return "Resposta comercial padrão"

    monkeypatch.setattr(reply_services, "OpenAIProvider", FakeProvider)

    result = generate_business_reply(
        classification="lead_novo",
        message="Quero saber mais sobre o serviço.",
        contact_name="Ana",
        conversation_summary="Contato iniciou conversa.",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
        contact_id="5511999991111",
    )

    assert result["source"] == "openai"
    assert result["reply"] == "Resposta comercial padrão"


def test_reply_service_returns_openai_finetuned_source_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "use_finetuned_model_for_replies", True)
    monkeypatch.setattr(settings, "openai_finetuned_model", "ft:gpt-4o-mini-2024-07-18:test")

    class FakeProvider:
        def generate_business_reply(self, **kwargs) -> str:
            return "Resposta comercial fine-tunada"

    monkeypatch.setattr(reply_services, "OpenAIProvider", FakeProvider)

    result = generate_business_reply(
        classification="lead_novo",
        message="Quero saber mais sobre o serviço.",
        contact_name="Ana",
        conversation_summary="Contato iniciou conversa.",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
        contact_id="5511999992222",
    )

    assert result["source"] == "openai_finetuned"
    assert result["reply"] == "Resposta comercial fine-tunada"