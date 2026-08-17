"""Geração da resposta comercial.

Fatos mutáveis vêm do company context publicado e da camada RAG; estilo vem
dos exemplos aprovados da empresa. O modelo não é fonte de verdade factual:
sem fonte relevante, a política da empresa decide entre pedir esclarecimento
ou encaminhar para um humano.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.taxonomy import normalize
from app.core.tenancy import require_company_id
from app.llm.providers.openai_provider import OpenAIProvider
from app.services.company_config_service import CompanyConfig, get_company_config
from app.services.knowledge_retrieval_service import (
    RetrievalResult,
    format_sources_for_prompt,
    retrieve_knowledge,
)
from app.services.model_registry_service import resolve_reply_model
from app.services.style_service import (
    format_style_examples_for_prompt,
    infer_style_scenario_type,
    select_style_examples,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReplyGeneration:
    reply: str
    source: str
    model_ref: str | None
    retrieval: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "source": self.source,
            "model_ref": self.model_ref,
            "retrieval": self.retrieval,
        }


def _effective_style_notes(style_examples: list[dict[str, Any]]) -> str | None:
    """Notas de estilo do exemplo mais relevante selecionado.

    O dataset grava a nota do próprio par; em runtime a nota equivalente é a
    do exemplo que está guiando o tom.
    """
    for example in style_examples:
        notes = str(example.get("style_notes") or "").strip()
        if notes:
            return notes
    return None


def _clarification_reply(
    *,
    contact_name: str | None,
    company_config: CompanyConfig,
) -> str:
    first_name = contact_name or "você"
    company_name = company_config.context.get("company_name") or "nossa equipe"
    return (
        f"Olá, {first_name}. Para te passar essa informação com precisão, "
        f"vou confirmar com {company_name} e já te retorno. "
        "Se puder, me diga um pouco mais sobre o que você precisa."
    )


def _fallback_business_reply(
    *,
    classification: str,
    contact_name: str | None,
    company_config: CompanyConfig,
) -> dict[str, Any]:
    first_name = contact_name or "você"
    company_context = company_config.context

    business_identity = company_context.get("business_identity", {})
    commercial_policy = company_context.get("commercial_policy", {})
    catalog = company_context.get("catalog", {})
    items = catalog.get("items", [])

    service_area = business_identity.get("service_area", "atendimento conforme disponibilidade")
    budget_policy = commercial_policy.get("budget_policy", "valores sob consulta")
    normalized_classification = normalize("classification", classification)

    if normalized_classification == "lead_novo":
        first_item = items[0] if items else {}
        first_service = first_item.get("name", "nossos serviços")
        first_price = first_item.get("price_range", "valor sob consulta")

        if not company_config.policy("allow_price_quotes", True):
            first_price = "valor sob consulta"

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

    if normalized_classification == "cliente_existente":
        return {
            "reply": (
                f"Olá, {first_name}. Recebi sua mensagem e vou te ajudar com isso. "
                "Se quiser, me envie mais detalhes para eu direcionar da forma mais adequada."
            ),
            "source": "template",
        }

    if normalized_classification == "suporte":
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
    customer_profile: str | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="business reply generation")
    company_config = get_company_config(resolved_company_id)

    scenario_type = infer_style_scenario_type(
        classification=classification,
        pipeline_stage=pipeline_stage,
    )

    retrieval = _retrieve_sources(
        company_config=company_config,
        message=message,
    )

    style_selection = select_style_examples(
        company_id=resolved_company_id,
        scenario_type=scenario_type,
        customer_profile=customer_profile,
        limit=3,
    ) if company_config.has_capability("style_examples") else None

    style_examples = style_selection.examples if style_selection else []
    style_examples_text = format_style_examples_for_prompt(style_examples)

    retrieval_trace: dict[str, Any] = {
        **retrieval.as_trace(),
        "scenario_type": scenario_type,
        "customer_profile": normalize("customer_profile", customer_profile),
        "style_strategy": style_selection.strategy if style_selection else "disabled",
        "style_example_ids": [example.get("id") for example in style_examples],
    }

    model_selection = resolve_reply_model(company_id=resolved_company_id)
    retrieval_trace["model_ref"] = model_selection.model_ref
    retrieval_trace["model_status"] = model_selection.status

    if settings.classification_provider != "openai":
        fallback = _fallback_business_reply(
            classification=classification,
            contact_name=contact_name,
            company_config=company_config,
        )
        return {**fallback, "model_ref": None, "retrieval": retrieval_trace}

    try:
        provider = OpenAIProvider()
        reply = provider.generate_business_reply(
            classification=classification,
            message=message,
            contact_name=contact_name,
            conversation_summary=conversation_summary,
            last_classification=last_classification,
            pipeline_stage=pipeline_stage,
            company_context=company_config.context,
            knowledge_sources_text=format_sources_for_prompt(retrieval),
            has_relevant_source=retrieval.has_relevant_source,
            require_source_for_facts=bool(
                company_config.policy("require_source_for_factual_claims", True)
            ),
            allow_price_quotes=bool(company_config.policy("allow_price_quotes", True)),
            style_examples_text=style_examples_text,
            model=model_selection.model_ref,
            # Campos do contrato compartilhado com o dataset de treino: o
            # modelo precisa ver em produção a mesma estrutura que viu no
            # treino, com os valores realmente calculados.
            scenario_type=scenario_type,
            customer_profile=normalize("customer_profile", customer_profile),
            profile_name=style_selection.profile_name if style_selection else None,
            style_notes=_effective_style_notes(style_examples),
            conversation_context=conversation_context or [],
        )
        return {
            "reply": reply,
            "source": model_selection.reply_source,
            "model_ref": model_selection.model_ref,
            "retrieval": retrieval_trace,
        }
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "reply.provider.fallback",
            provider="openai",
            error_type=type(exc).__name__,
            classification=classification,
            company_id=resolved_company_id,
        )
        fallback = _fallback_business_reply(
            classification=classification,
            contact_name=contact_name,
            company_config=company_config,
        )
        return {**fallback, "model_ref": None, "retrieval": retrieval_trace}


def _retrieve_sources(*, company_config: CompanyConfig, message: str) -> RetrievalResult:
    if not company_config.has_capability("knowledge_rag"):
        return RetrievalResult(
            company_id=company_config.company_id,
            query=message,
            chunks=[],
            mode="capability_disabled",
            candidates_considered=0,
            has_relevant_source=False,
        )

    return retrieve_knowledge(
        company_id=company_config.company_id,
        query=message,
        limit=int(company_config.policy("max_knowledge_chunks", settings.knowledge_max_chunks)),
    )
