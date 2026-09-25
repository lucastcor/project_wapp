from types import SimpleNamespace

from app.core.config import settings
from app.llm.providers.business_provider import BusinessLLMProvider
from app.services import reply_services
from app.services.reply_services import generate_business_reply


def _company_id() -> int:
    """Empresa real para as chamadas de geração de resposta.

    `generate_business_reply` recupera conhecimento e exemplos de estilo, e
    ambos passaram a exigir `company_id`: sem ele, a recuperação devolveria o
    conteúdo de todas as empresas. Os testes aqui verificam a geração da
    resposta, não o escopo — então usam uma empresa de verdade, como o
    orquestrador faz.
    """
    from app.storage.repository import RuntimeRepository

    return RuntimeRepository().ensure_default_company()



def test_reply_service_fallback_for_lead(monkeypatch) -> None:
    monkeypatch.setattr(settings, "classification_provider", "mock")

    result = generate_business_reply(
        classification="lead_novo",
        message="Quero saber mais sobre o serviço.",
        contact_name="Ana",
        conversation_summary="Contato iniciou conversa.",
        last_classification="lead_novo",
        pipeline_stage="novo_lead",
        company_id=_company_id(),
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
        company_id=_company_id(),
    )

    assert result["source"] == "none"
    assert result["reply"] == ""


def test_business_prompt_uses_fixed_catalog_price_as_authoritative_source() -> None:
    class CapturingProvider(BusinessLLMProvider):
        model = "test-model"

        def __init__(self) -> None:
            self.prompt = ""

        def _generate(self, *, model, input, text=None):
            self.prompt = input
            return SimpleNamespace(output_text="A consulta custa R$ 150,00.")

    provider = CapturingProvider()
    reply = provider.generate_business_reply(
        classification="lead_novo",
        message="Quanto custa a consulta?",
        company_context={
            "catalog": {
                "items": [
                    {
                        "name": "Consulta inicial",
                        "description": "Consulta presencial",
                        "price": 150.0,
                        "price_mode": "fixed",
                    }
                ]
            }
        },
        require_source_for_facts=True,
        has_relevant_source=False,
    )

    assert reply == "A consulta custa R$ 150,00."
    assert "Preço cadastrado: R$ 150,00" in provider.prompt
    assert "catálogo disponível acima também são fontes autorizadas" in provider.prompt


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
        company_id=_company_id(),
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
        company_id=_company_id(),
    )

    assert result["source"] == "openai_finetuned"
    assert result["reply"] == "Resposta comercial fine-tunada"
