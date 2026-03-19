from typing import Any

from app.core.config import settings
from app.core.company_context import get_company_context
from app.llm.providers.openai_provider import OpenAIProvider
from app.storage.repository import RuntimeRepository
from app.services.style_service import (
    format_style_examples_for_prompt,
    get_relevant_style_examples,
    infer_style_scenario_type,
)

repository = RuntimeRepository()


def _fallback_business_reply(
    classification: str,
    message: str,
    contact_name: str | None = None,
    conversation_summary: str | None = None,
    last_classification: str | None = None,
    pipeline_stage: str | None = None,
    relevant_knowledge: list[dict[str, Any]] | None = None,
    style_examples_text: str | None = None,
    company_slug: str | None = None,
) -> dict[str, Any]:
    first_name = contact_name or "você"
    company_context = get_company_context(company_slug=company_slug)

    business_identity = company_context.get("business_identity", {})
    business_hours = company_context.get("business_hours", {})
    commercial_policy = company_context.get("commercial_policy", {})
    catalog = company_context.get("catalog", {})
    items = catalog.get("items", [])

    service_area = business_identity.get("service_area", "atendimento conforme disponibilidade")
    weekday_hours = business_hours.get("weekdays", "horário comercial")
    budget_policy = commercial_policy.get("budget_policy", "valores sob consulta")

    if classification == "lead_novo":
        first_item = items[0] if items else {}
        first_service = first_item.get("name", "nossos serviços")
        first_price = first_item.get("price_range", "valor sob consulta")

        return {
            "reply": (
                f"Olá, {first_name}. Obrigado pelo contato. "
                f"Atendemos {service_area}. "
                f"Um dos serviços disponíveis é {first_service}, com faixa de valor {first_price}. "
                f"Os valores finais seguem nossa política comercial: {budget_policy} "
                "Para eu te orientar melhor, me diga qual serviço você está buscando e qual é a sua necessidade."
            ),
            "source": "template",
        }

    if classification == "cliente_existente":
        return {
            "reply": (
                f"Olá, {first_name}. Recebi sua mensagem e vou te ajudar com isso. "
                "Se quiser, me envie mais detalhes para eu direcionar da forma mais adequada."
            ),
            "source": "template",
        }

    if classification == "suporte":
        return {
            "reply": (
                f"Olá, {first_name}. Recebi sua solicitação de suporte. "
                "Pode me informar mais detalhes do problema para eu direcionar corretamente?"
            ),
            "source": "template",
        }

    return {
        "reply": "",
        "source": "none",
    }


def generate_business_reply(
    classification: str,
    message: str,
    contact_name: str | None = None,
    conversation_summary: str | None = None,
    last_classification: str | None = None,
    pipeline_stage: str | None = None,
    contact_id: str | None = None,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, Any]:
    relevant_knowledge = repository.get_relevant_knowledge_entries(
        contact_id=contact_id,
        company_id=company_id,
    )
    scenario_type = infer_style_scenario_type(
        classification=classification,
        pipeline_stage=pipeline_stage,
    )
    style_examples = get_relevant_style_examples(
        scenario_type=scenario_type,
        limit=3,
        company_id=company_id,
    )
    style_examples_text = format_style_examples_for_prompt(style_examples)
    if settings.classification_provider != "openai":
        return _fallback_business_reply(
            classification=classification,
            message=message,
            contact_name=contact_name,
            conversation_summary=conversation_summary,
            last_classification=last_classification,
            pipeline_stage=pipeline_stage,
            relevant_knowledge=relevant_knowledge,
            style_examples_text=style_examples_text,
            company_slug=company_slug,
        )

    try:
        reply_source = (
            "openai_finetuned"
            if settings.use_finetuned_model_for_replies and settings.openai_finetuned_model
            else "openai"
        )
        provider = OpenAIProvider()
        reply = provider.generate_business_reply(
            classification=classification,
            message=message,
            contact_name=contact_name,
            conversation_summary=conversation_summary,
            last_classification=last_classification,
            pipeline_stage=pipeline_stage,
            relevant_knowledge=relevant_knowledge,
            style_examples_text=style_examples_text,
            company_slug=company_slug,
        )
        return {
            "reply": reply,
            "source": reply_source,
        }
    except Exception as e:
        print("OPENAI REPLY ERROR:", repr(e))
        return _fallback_business_reply(
            classification=classification,
            message=message,
            contact_name=contact_name,
            conversation_summary=conversation_summary,
            last_classification=last_classification,
            pipeline_stage=pipeline_stage,
            relevant_knowledge=relevant_knowledge,
            style_examples_text=style_examples_text,
            company_slug=company_slug,
        )