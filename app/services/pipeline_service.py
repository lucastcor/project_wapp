import logging
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.llm.providers.openai_provider import OpenAIProvider

VALID_PIPELINE_STAGES = {
    "novo_lead",
    "qualificando",
    "aguardando_confirmacao",
    "proposta_em_preparacao",
    "aguardando_resposta",
    "convertido",
    "encerrado",
}

VALID_LEAD_TEMPERATURES = {"cold", "warm", "hot"}
logger = get_logger(__name__)


def _fallback_pipeline_state(
    conversation_summary: str | None,
    latest_message: str,
    latest_classification: str,
) -> dict[str, Any]:
    normalized = latest_message.lower()
    summary = (conversation_summary or "").lower()
    combined = f"{summary} {normalized}".strip()

    if latest_classification != "lead_novo":
        if latest_classification == "agendamento":
            return {
                "pipeline_stage": "aguardando_confirmacao",
                "next_action": "Conduzir confirmação do horário e próximos dados operacionais do agendamento.",
                "lead_temperature": "hot",
            }
        return {
            "pipeline_stage": "encerrado",
            "next_action": "Avaliar manualmente o atendimento fora do fluxo comercial.",
            "lead_temperature": "cold",
        }

    if any(term in combined for term in ["quero fechar", "contratar", "fechar", "proposta", "contrato"]):
        return {
            "pipeline_stage": "aguardando_confirmacao",
            "next_action": "Confirmar serviço, modalidade, disponibilidade e dados para proposta ou contrato.",
            "lead_temperature": "hot",
        }

    if any(term in combined for term in ["valor", "preço", "orcamento", "orçamento", "quanto custa"]):
        return {
            "pipeline_stage": "qualificando",
            "next_action": "Entender escopo da necessidade e alinhar informações para estimativa comercial.",
            "lead_temperature": "warm",
        }

    return {
        "pipeline_stage": "novo_lead",
        "next_action": "Entender necessidade do contato e coletar contexto inicial do atendimento.",
        "lead_temperature": "warm",
    }


def infer_pipeline_state(
    conversation_summary: str | None,
    latest_message: str,
    latest_classification: str,
) -> dict[str, Any]:
    if settings.classification_provider != "openai":
        return _fallback_pipeline_state(
            conversation_summary=conversation_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
        )

    try:
        provider = OpenAIProvider()
        result = provider.infer_pipeline_state(
            conversation_summary=conversation_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
        )

        pipeline_stage = result.get("pipeline_stage", "novo_lead")
        next_action = result.get(
            "next_action",
            "Entender necessidade do contato e definir próximo passo do atendimento.",
        )
        lead_temperature = result.get("lead_temperature", "warm")

        if pipeline_stage not in VALID_PIPELINE_STAGES:
            pipeline_stage = "novo_lead"

        if lead_temperature not in VALID_LEAD_TEMPERATURES:
            lead_temperature = "warm"

        return {
            "pipeline_stage": pipeline_stage,
            "next_action": next_action,
            "lead_temperature": lead_temperature,
        }
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "pipeline.provider.fallback",
            provider="openai",
            error_type=type(exc).__name__,
        )
        return _fallback_pipeline_state(
            conversation_summary=conversation_summary,
            latest_message=latest_message,
            latest_classification=latest_classification,
        )
